"""
[소스: data.krx.co.kr 전종목기본정보 - 수동 다운로드] 국내ETF 총보수 -> 원본 컬럼 cu_charge_rt 결측치만 직접 채움

★ 변경사항 (사이드카 -> 직접 채움)
- krx_charge_rt 같은 새 컬럼을 만들지 않고, cu_charge_rt 자체의 빈 칸만 채움
- 이미 값이 있는 셀은 절대 덮어쓰지 않음
- base_dt는 마스터가 아니라 별도 audit_log.csv에 기록

필요 패키지: pip install pandas openpyxl --break-system-packages
API 키 불필요 (수동 다운로드 파일만 있으면 됨)
"""

import os
import pandas as pd

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref01n001_국내ETF마스터_data.xlsx"
# ⭐ 아래 두 경로는 01/02/04/07 (국내ETF 스크립트 4개)가 전부 동일하게 공유합니다.
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF_audit_log.csv"

# data.krx.co.kr에서 다운받은 전종목 기본정보 파일들 (표준코드/총보수 컬럼 포함)
KRX_FEE_FILES = [
    {"path": r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\external_data\data_3924_20260824.xlsx", "label": "ETF", "download_date": "20260824"},
    {"path": r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\external_data\data_3937_20260824.xlsx", "label": "ETN", "download_date": "20260824"},
]

TARGET_COL = "cu_charge_rt"
# ======================================================


def is_blank(v) -> bool:
    if pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def load_master(original_path: str, output_path: str) -> pd.DataFrame:
    """출력 파일이 이미 있으면(다른 스크립트가 먼저 채워둔 것) 그걸 이어서 읽고,
    없으면 원본 마스터에서 새로 시작한다."""
    if os.path.exists(output_path):
        print(f"기존 누적 결과 파일 발견, 이어서 사용: {output_path}")
        return pd.read_excel(output_path, dtype=str, keep_default_na=False, na_values=[])
    print(f"누적 결과 파일 없음, 원본 마스터에서 새로 시작: {original_path}")
    return pd.read_excel(original_path, dtype=str, keep_default_na=False, na_values=[])


def append_audit_log(audit_rows: list[dict], audit_log_path: str):
    """audit_log.csv를 덮어쓰지 않고 이어붙인다 (여러 스크립트가 같은 로그를 공유)."""
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


def main():
    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)")
    master["pd_itm_no"] = master["pd_itm_no"].astype(str).str.strip()

    fee_map = {}
    fee_date_map = {}
    for src in KRX_FEE_FILES:
        df = pd.read_excel(src["path"])
        if "표준코드" not in df.columns or "총보수" not in df.columns:
            print(f"⚠️ [{src['label']}] '표준코드' 또는 '총보수' 컬럼이 없습니다: {df.columns.tolist()}")
            continue
        print(f"[{src['label']}] 로드: {len(df)}행 (다운로드일: {src['download_date']})")
        for isin, rate in zip(df["표준코드"].astype(str).str.strip(), df["총보수"]):
            if isin not in fee_map:
                fee_map[isin] = rate
                fee_date_map[isin] = src["download_date"]

    missing_mask = master[TARGET_COL].apply(is_blank)
    target_idx = master[missing_mask].index
    print(f"{TARGET_COL} 결측: {len(target_idx)}건")

    audit_rows = []
    filled = 0
    for idx in target_idx:
        isin = master.at[idx, "pd_itm_no"]
        if isin in fee_map:
            master.at[idx, TARGET_COL] = fee_map[isin]
            filled += 1
            audit_rows.append({
                "pd_itm_no": isin,
                "column": TARGET_COL,
                "filled_value": fee_map[isin],
                "source": "data.krx.co.kr 전종목기본정보 (수동다운로드)",
                "base_dt": fee_date_map.get(isin, ""),
            })

    print(f"\n{TARGET_COL} 신규 채움: {filled}/{len(target_idx)}건")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"저장 완료 (원본과 동일한 컬럼 구조, 다른 국내ETF 스크립트와 같은 파일에 누적): {OUTPUT_PATH}")

    append_audit_log(audit_rows, AUDIT_LOG_PATH)
    if audit_rows:
        print(f"감사 로그(출처 추적용, 여러 스크립트가 공유/누적) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
