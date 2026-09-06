from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol


# Keep this script runnable as `python fund-db-handoff/embed_overseas.py`.
# Adding the repository root lets ingestion and the API share one contract.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from b_agent.embeddings import (  # noqa: E402
    EmbeddingSettings,
    embedding_settings_from_env,
    validated_embedding_vector,
)


class CursorLike(Protocol):
    def execute(self, query: str, params: Sequence[Any] | None = None) -> None: ...

    def fetchall(self) -> list[Any]: ...

    def fetchone(self) -> Any: ...

    def close(self) -> None: ...


class ConnectionLike(Protocol):
    def cursor(self) -> CursorLike: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


TARGETS_SQL = """
    SELECT document_id, normalized_text
    FROM search.overseas_etf_strategy
    WHERE embedding IS NULL
       OR embedding_model IS DISTINCT FROM %s
    ORDER BY document_id
"""

STATUS_SQL = """
    SELECT
        count(*) AS strategy_documents,
        count(*) FILTER (WHERE embedding IS NULL) AS missing_embeddings,
        count(*) FILTER (
            WHERE embedding IS NOT NULL AND embedding_model = %s
        ) AS current_model_embeddings,
        count(*) FILTER (
            WHERE embedding IS NOT NULL
              AND embedding_model IS DISTINCT FROM %s
        ) AS mismatched_model_embeddings
    FROM search.overseas_etf_strategy
"""

UPDATE_SQL = """
    UPDATE search.overseas_etf_strategy
    SET embedding = %s::vector,
        embedding_model = %s,
        embedded_at = now()
    WHERE document_id = %s
"""


def _positive_batch_size() -> int:
    raw = os.getenv("EMBEDDING_BATCH_SIZE", "8")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("EMBEDDING_BATCH_SIZE must be an integer") from exc
    if value <= 0:
        raise ValueError("EMBEDDING_BATCH_SIZE must be positive")
    return value


def _build_model(settings: EmbeddingSettings) -> Any:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(settings.model_name, truncate_dim=settings.dimension)
    model.max_seq_length = settings.max_seq_length
    return model


def fetch_embedding_targets(
    cursor: CursorLike, model_name: str
) -> list[tuple[int, str]]:
    cursor.execute(TARGETS_SQL, (model_name,))
    return list(cursor.fetchall())


def read_embedding_status(cursor: CursorLike, model_name: str) -> dict[str, int]:
    cursor.execute(STATUS_SQL, (model_name, model_name))
    row = cursor.fetchone()
    return {
        "strategy_documents": int(row[0]),
        "missing_embeddings": int(row[1]),
        "current_model_embeddings": int(row[2]),
        "mismatched_model_embeddings": int(row[3]),
    }


def _print_status(model_name: str, status: dict[str, int]) -> None:
    print(f"임베딩 모델: {model_name}")
    for key, value in status.items():
        print(f"{key}={value}")


def generate_embeddings(
    rows: list[tuple[int, str]],
    settings: EmbeddingSettings,
    batch_size: int,
    model_factory: Callable[[EmbeddingSettings], Any] = _build_model,
) -> list[tuple[int, list[float]]]:
    model = model_factory(settings)
    encoded = model.encode(
        [row[1] for row in rows],
        batch_size=batch_size,
        normalize_embeddings=True,
    )
    if len(encoded) != len(rows):
        raise RuntimeError(
            "embedding result count mismatch: "
            f"expected {len(rows)}, received {len(encoded)}"
        )
    return [
        (
            document_id,
            validated_embedding_vector(vector, settings.dimension, label="document"),
        )
        for (document_id, _), vector in zip(rows, encoded, strict=True)
    ]


def update_embeddings(
    cursor: CursorLike,
    embeddings: list[tuple[int, list[float]]],
    model_name: str,
) -> None:
    for document_id, vector in embeddings:
        vector_value = "[" + ",".join(map(str, vector)) + "]"
        cursor.execute(UPDATE_SQL, (vector_value, model_name, document_id))


def run(
    connection: ConnectionLike,
    settings: EmbeddingSettings,
    *,
    batch_size: int,
    status_only: bool = False,
    model_factory: Callable[[EmbeddingSettings], Any] = _build_model,
) -> dict[str, int]:
    cursor = connection.cursor()
    try:
        if status_only:
            status = read_embedding_status(cursor, settings.model_name)
            _print_status(settings.model_name, status)
            return status

        rows = fetch_embedding_targets(cursor, settings.model_name)
        print(f"대상 {len(rows)}건 임베딩 시작")
        if rows:
            started_at = time.time()
            embeddings = generate_embeddings(
                rows,
                settings,
                batch_size,
                model_factory=model_factory,
            )
            print(f"임베딩 생성 완료: {time.time() - started_at:.1f}초")
            update_embeddings(cursor, embeddings, settings.model_name)

        status = read_embedding_status(cursor, settings.model_name)
        if status["missing_embeddings"] or status["mismatched_model_embeddings"]:
            raise RuntimeError(
                "embedding verification failed: "
                f"missing={status['missing_embeddings']}, "
                f"model_mismatch={status['mismatched_model_embeddings']}"
            )

        connection.commit()
        _print_status(settings.model_name, status)
        print("DB 저장 및 검증 완료" if rows else "현재 모델의 임베딩이 이미 최신입니다.")
        return status
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="해외 ETF 전략문 임베딩을 현재 모델 설정과 동기화합니다."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="현재 모델 일치/불일치 임베딩 건수만 출력합니다.",
    )
    args = parser.parse_args(argv)

    # Validate the fixed vector(1024) contract before opening the database.
    settings = embedding_settings_from_env()
    batch_size = _positive_batch_size()

    import psycopg2

    from db_config import psycopg2_connection_kwargs

    connection = psycopg2.connect(**psycopg2_connection_kwargs())
    connection.autocommit = False
    try:
        run(
            connection,
            settings,
            batch_size=batch_size,
            status_only=args.status,
        )
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
