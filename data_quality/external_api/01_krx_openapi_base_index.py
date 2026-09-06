"""
[소스: KRX Open API] 국내ETF 기초지수 -> 원본 컬럼 cu_base_index 결측치만 직접 채움

★ 변경사항 (사이드카 -> 직접 채움)
- 더 이상 krx_base_index 같은 새 컬럼을 만들지 않음
- 마스터 원본을 통째로 복사한 뒤, cu_base_index가 결측(빈 문자열/공백)인 셀에만
  KRX API 값을 직접 씀. 이미 값이 있는 셀은 절대 덮어쓰지 않음
- 어떤 행이 이번에 채워졌는지는 마스터가 아니라 별도 audit_log.csv에 기록
  (마스터 구조 자체는 원본과 동일하게 유지하기 위함)

필요 패키지: pip install python-dotenv requests pandas openpyxl --break-system-packages
"""

import os
import sys
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref01n001_국내ETF마스터_data.xlsx"
# ⭐ 아래 두 경로는 01/02/04/07 (국내ETF 스크립트 4개)가 전부 동일하게 공유합니다.
#    출력 파일이 이미 있으면 거기서 이어서 채우고, 없으면 원본에서 새로 시작합니다.
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF_audit_log.csv"

BASE_DATE = "20260821"  # 마스터 기준일에 맞춰 조회 (수정 가능)

ETF_URL = "http://data-dbg.krx.co.kr/svc/apis/etp/etf_bydd_trd"
ETN_URL = "http://data-dbg.krx.co.kr/svc/apis/etp/etn_bydd_trd"

TARGET_COL = "cu_base_index"
# ======================================================

API_KEY = os.getenv("KRX_API_KEY")
if not API_KEY:
    print("⚠️ .env 파일에 KRX_API_KEY가 설정되지 않았습니다.")
    sys.exit(1)


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


def fetch_krx(url: str, base_date: str, label: str) -> pd.Series:
    """ISU_CD(단축코드) -> IDX_IND_NM(기초지수명) Series 반환"""
    headers = {"AUTH_KEY": API_KEY}
    params = {"basDd": base_date}

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    data = resp.json()

    list_key = next((k for k, v in data.items() if isinstance(v, list)), None)
    if list_key is None or len(data.get(list_key, [])) == 0:
        headers_post = {**headers, "Content-Type": "application/json", "Accept": "application/json"}
        resp = requests.post(url, headers=headers_post, json=params, timeout=30)
        data = resp.json()
        list_key = next((k for k, v in data.items() if isinstance(v, list)), None)

    if list_key is None or len(data.get(list_key, [])) == 0:
        print(f"⚠️ [{label}] 응답이 비어있습니다. BASE_DATE(영업일 여부)를 확인하세요.")
        return pd.Series(dtype=object)

    df = pd.DataFrame(data[list_key])
    df["ISU_CD"] = df["ISU_CD"].astype(str).str.strip()
    print(f"[{label}] 응답: {len(df)}건")
    return df.set_index("ISU_CD")["IDX_IND_NM"]


def main():
    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)")

    master["pd_itm_no"] = master["pd_itm_no"].astype(str).str.strip()
    match_key = master["pd_itm_no_ma"].astype(str).str[1:].str.strip()

    idx_etf = fetch_krx(ETF_URL, BASE_DATE, "ETF")
    time.sleep(0.3)
    idx_etn = fetch_krx(ETN_URL, BASE_DATE, "ETN")

    idx_map = pd.concat([idx_etf, idx_etn])
    idx_map = idx_map[~idx_map.index.duplicated(keep="first")]

    # 결측인 행만 대상으로 함
    missing_mask = master[TARGET_COL].apply(is_blank)
    target_idx = master[missing_mask].index
    print(f"{TARGET_COL} 결측: {len(target_idx)}건")

    audit_rows = []
    filled = 0
    for idx in target_idx:
        key = match_key.loc[idx]
        val = idx_map.get(key)
        if val is not None and not is_blank(val):
            master.at[idx, TARGET_COL] = val
            filled += 1
            audit_rows.append({
                "pd_itm_no": master.at[idx, "pd_itm_no"],
                "column": TARGET_COL,
                "filled_value": val,
                "source": "KRX Open API (etf_bydd_trd/etn_bydd_trd)",
                "base_dt": BASE_DATE,
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
