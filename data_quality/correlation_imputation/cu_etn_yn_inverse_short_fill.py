"""
cu_etn_yn_inverse_short_fill.py

목적:
    해외ETF 마스터의 cu_etn_yn(ETN여부), cu_inverse_short_yn(인버스/숏여부)
    결측치 중, "진짜 Y가 맞다"고 확신할 수 있는 것만 채운다.
    나머지(확신 없는 결측)는 억지로 N을 채우지 않고 결측인 채로 그대로 둔다.

채우는 규칙 (Y만 채움, N은 채우지 않음):
    ① cu_etn_yn:
       - 상품명에 'ETN' 또는 'Exchange Traded Notes'가 있으면 -> 'Y'
         (단, 'Vietnam' 같은 단어에 우연히 'etn'이 포함되는 오탐은 제외)
       -> 검증 결과: 결측 5,972건 중 1건(ETNG.K)만 채워지고, 나머지 5,971건은 그대로 결측

    ② cu_inverse_short_yn:
       - cu_lev_fector(추적배수)가 음수면 -> 'Y'
         (인버스 상품은 배수가 음수로 표기되므로, 이미 검증된 사실을 그대로 활용.
          cu_lev_fector_fill.py를 먼저 실행해서 배수를 최대한 채운 뒤 이 스크립트를
          돌리면 새로 채워진 음수값들까지 자동으로 반영되어 더 정확해짐)
       -> 검증 결과: 결측 5,854건 중 3건만 채워지고, 나머지 5,851건은 그대로 결측

⚠️ 실행 순서 권장:
    01/02/04/07(국내ETF), 05(해외ETF 시세), cu_lev_fector_fill.py를 먼저 돌리고
    나서 이 스크립트를 마지막에 실행하는 것을 권장 (cu_lev_fector가 최대한 채워진
    상태에서 cu_inverse_short_yn 교차검증을 해야 새로 발견되는 예외까지 놓치지 않음).

사용법: python cu_etn_yn_inverse_short_fill.py
"""

import os
import pandas as pd
from datetime import datetime

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref02n001_해외ETF마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF_audit_log.csv"

NAME_COL = "pd_nm"
# ======================================================


def is_blank(v) -> bool:
    if pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def load_master(original_path: str, output_path: str) -> pd.DataFrame:
    if os.path.exists(output_path):
        print(f"기존 누적 결과 파일 발견, 이어서 사용: {output_path}")
        return pd.read_excel(output_path, dtype=str, keep_default_na=False, na_values=[])
    print(f"누적 결과 파일 없음, 원본 마스터에서 새로 시작: {original_path}")
    return pd.read_excel(original_path, dtype=str, keep_default_na=False, na_values=[])


def append_audit_log(audit_rows: list[dict], audit_log_path: str):
    if not audit_rows:
        return
    new_df = pd.DataFrame(audit_rows)
    if os.path.exists(audit_log_path):
        old_df = pd.read_csv(audit_log_path, dtype=str)
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df
    os.makedirs(os.path.dirname(audit_log_path), exist_ok=True)
    combined.to_csv(audit_log_path, index=False, encoding="utf-8-sig")


def fill_etn_yn(master: pd.DataFrame, audit_rows: list, now_str: str):
    col = "cu_etn_yn"
    missing_mask = master[col].apply(is_blank)
    before = missing_mask.sum()

    name = master[NAME_COL].astype(str)
    has_etn_pattern = name.str.contains("ETN|Exchange Traded Notes", case=False, na=False)
    is_false_positive = name.str.contains("Vietnam", case=False, na=False)
    is_true_etn = has_etn_pattern & ~is_false_positive

    # Y로 확신되는 것만 채움. 나머지는 그대로 결측으로 둠 (N을 채우지 않음)
    to_fill = missing_mask & is_true_etn
    for idx in master.loc[to_fill].index:
        master.at[idx, col] = "Y"
        audit_rows.append({
            "pd_itm_no": master.at[idx, "pd_itm_no"], "column": col,
            "filled_value": "Y",
            "source": "internal_rule(상품명 ETN패턴, Vietnam오탐제외)",
            "filled_at": now_str,
        })

    after = master[col].apply(is_blank).sum()
    print(f"{col} 결측: {before} -> {after} (Y로 채움: {to_fill.sum()}건, 나머지는 결측 유지)")
    return master


def fill_inverse_short_yn(master: pd.DataFrame, audit_rows: list, now_str: str):
    col = "cu_inverse_short_yn"
    missing_mask = master[col].apply(is_blank)
    before = missing_mask.sum()

    lev = pd.to_numeric(master["cu_lev_fector"], errors="coerce")
    is_negative = lev < 0

    # Y로 확신되는 것만 채움. 나머지는 그대로 결측으로 둠 (N을 채우지 않음)
    to_fill = missing_mask & is_negative.fillna(False)
    for idx in master.loc[to_fill].index:
        master.at[idx, col] = "Y"
        audit_rows.append({
            "pd_itm_no": master.at[idx, "pd_itm_no"], "column": col,
            "filled_value": "Y",
            "source": "internal_rule(cu_lev_fector 음수)",
            "filled_at": now_str,
        })

    after = master[col].apply(is_blank).sum()
    print(f"{col} 결측: {before} -> {after} (Y로 채움: {to_fill.sum()}건, 나머지는 결측 유지)")
    return master


def main():
    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)\n")

    audit_rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    master = fill_etn_yn(master, audit_rows, now_str)
    master = fill_inverse_short_yn(master, audit_rows, now_str)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"\n저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    if audit_rows:
        append_audit_log(audit_rows, AUDIT_LOG_PATH)
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
