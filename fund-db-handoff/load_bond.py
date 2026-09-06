from pathlib import Path
import getpass
import hashlib
import json
import unicodedata

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb


DATASETS = [
    {
        "dataset_code": "DOMESTIC_BOND",
        "file_token": "국내채권마스터_filled",
        "key_column": "pd_no",
    },
]


def normalize_name(value):
    return unicodedata.normalize("NFC", value)


def find_excel(file_token):
    desktop = Path.home() / "Desktop"
    matches = [
        path
        for path in desktop.glob("*.xlsx")
        if normalize_name(file_token) in normalize_name(path.stem)
    ]

    if len(matches) != 1:
        raise FileNotFoundError(file_token)

    return matches[0]


def calculate_sha256(file_path):
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def clean_value(value):
    if value is None or pd.isna(value):
        return None

    text = str(value)

    if text.strip() == "":
        return None

    return text


def load_dataset(connection, config):
    file_path = find_excel(config["file_token"])
    file_hash = calculate_sha256(file_path)

    print(f"\n[{config['dataset_code']}] {file_path.name} 읽는 중...")

    sheets = pd.read_excel(
        file_path,
        sheet_name=None,
        dtype=str,
        keep_default_na=False,
        engine="openpyxl",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO meta.dataset_snapshot (
                dataset_code,
                source_file_name,
                file_sha256,
                load_status
            )
            VALUES (%s, %s, %s, 'LOADING')
            ON CONFLICT (dataset_code, file_sha256)
            DO UPDATE SET
                source_file_name = EXCLUDED.source_file_name,
                load_status = 'LOADING',
                loaded_at = now()
            RETURNING snapshot_id
            """,
            (
                config["dataset_code"],
                file_path.name,
                file_hash,
            ),
        )

        snapshot_id = cursor.fetchone()[0]
        total_rows = 0

        insert_sql = """
            INSERT INTO raw.source_row (
                snapshot_id,
                dataset_code,
                source_sheet,
                source_row_number,
                source_key,
                payload,
                row_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (
                snapshot_id,
                source_sheet,
                source_row_number
            )
            DO UPDATE SET
                dataset_code = EXCLUDED.dataset_code,
                source_key = EXCLUDED.source_key,
                payload = EXCLUDED.payload,
                row_hash = EXCLUDED.row_hash,
                validation_errors = '[]'::jsonb,
                loaded_at = now()
        """

        for sheet_name, dataframe in sheets.items():
            columns = [str(column) for column in dataframe.columns]

            if config["key_column"] not in columns:
                raise ValueError(
                    f"{file_path.name}/{sheet_name}에 "
                    f"{config['key_column']} 컬럼이 없습니다."
                )

            batch = []

            for excel_row_number, values in enumerate(
                dataframe.itertuples(index=False, name=None),
                start=2,
            ):
                payload = {
                    column: clean_value(value)
                    for column, value in zip(columns, values)
                }

                key_values = [
                    str(payload.get("pd_no", "")).strip(),
                    str(payload.get("pd_exg_mkt", "")).strip(),
                    str(payload.get("info_seq", "")).strip(),
                ]
                source_key = "|".join(key_values) if all(key_values) else None

                payload_text = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )

                row_hash = hashlib.sha256(
                    payload_text.encode("utf-8")
                ).hexdigest()

                batch.append(
                    (
                        snapshot_id,
                        config["dataset_code"],
                        sheet_name,
                        excel_row_number,
                        source_key,
                        Jsonb(payload),
                        row_hash,
                    )
                )

                if len(batch) == 500:
                    cursor.executemany(insert_sql, batch)
                    total_rows += len(batch)
                    batch.clear()

            if batch:
                cursor.executemany(insert_sql, batch)
                total_rows += len(batch)

            print(f"  - {sheet_name}: {len(dataframe):,}행")

        cursor.execute(
            """
            UPDATE meta.dataset_snapshot
            SET
                row_count = %s,
                load_status = 'LOADED',
                loaded_at = now()
            WHERE snapshot_id = %s
            """,
            (total_rows, snapshot_id),
        )

    connection.commit()
    print(f"  완료: {total_rows:,}행 적재")


def main():
    password = getpass.getpass("PostgreSQL postgres 비밀번호: ")

    with psycopg.connect(
        host="localhost",
        port=5432,
        dbname="fund_ontology",
        user="postgres",
        password=password,
    ) as connection:
        for config in DATASETS:
            try:
                load_dataset(connection, config)
            except Exception:
                connection.rollback()
                raise

    print("\n세 파일 적재가 모두 완료되었습니다.")


if __name__ == "__main__":
    main()
