"""
[소스: SEIBro(증권예탁결제원) ETF종합정보 API] 국내ETF 배당주기 -> 원본 컬럼 pd_dvid_cycl 결측치만 직접 채움

★ 변경사항 (사이드카 -> 직접 채움)
- seibro_dvid_cycl_code 같은 새 컬럼을 만들지 않고, pd_dvid_cycl 자체의 빈 칸만 채움
- 실제 마스터를 확인해보니 pd_dvid_cycl은 이미 'M'/'Q'/'S'/'A' 코드로 저장되어 있어서,
  월배당/분기배당/반기배당/연배당으로 판정된 것만 그대로 채우면 됨
  (무배당/확인불가/비정기배당은 코드가 없어 채우지 않고 건너뜀 — 기존과 동일)
- 어떤 행/값이 채워졌는지는 마스터가 아니라 별도 audit_log.csv에 기록

필요 패키지: pip install python-dotenv requests pandas openpyxl --break-system-packages
"""

import os
import sys
import time
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref01n001_국내ETF마스터_data.xlsx"
# ⭐ 아래 두 경로는 01/02/04/07 (국내ETF 스크립트 4개)가 전부 동일하게 공유합니다.
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF_audit_log.csv"

CUTOFF_DATE = "20260824"  # 마스터 기준일. 이 날짜 이전 지급 이력만 사용
TARGET_COL = "pd_dvid_cycl"
# ======================================================

SEIBRO_COOKIES_RAW = os.getenv("SEIBRO_COOKIES")
if not SEIBRO_COOKIES_RAW:
    print("⚠️ .env 파일에 SEIBRO_COOKIES가 설정되지 않았습니다.")
    sys.exit(1)

URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
HEADERS = {
    "Accept": "application/xml",
    "Content-Type": 'application/xml; charset="UTF-8"',
    "Origin": "https://seibro.or.kr",
    "Referer": "https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/etf/BIP_CNTS906032V.xml&menuNo=514",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "submissionid": "submission_compstInfoSecnDetailList",
}
_INITIAL_COOKIES = dict(item.split("=", 1) for item in SEIBRO_COOKIES_RAW.split("; "))
SESSION = requests.Session()
SESSION.cookies.update(_INITIAL_COOKIES)

CYCLE_CODE_MAP = {"월배당": "M", "분기배당": "Q", "반기배당": "S", "연배당": "A"}  # 무배당/확인불가/비정기배당은 채울 코드 없음


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


def fetch_distribution_history(isin: str) -> list[str]:
    body = (
        f'<reqParam action="compstInfoSecnDetailList" '
        f'task="ksd.safe.bip.cnts.etf.process.EtfCompstInfoPTask">'
        f'<MENU_NO value="514"/>'
        f'<CMM_BTN_ABBR_NM value="total_search,openall,print,hwp,word,pdf,searchIcon,seach,'
        f'favorites float_left,more float_right,more float_right,more float_right,more float_right,"/>'
        f'<W2XPATH value="/IPORTAL/user/etf/BIP_CNTS906032V.xml"/>'
        f'<isin value="{isin}"/></reqParam>'
    )
    resp = SESSION.post(URL, headers=HEADERS, data=body.encode("utf-8"), timeout=15)
    root = ET.fromstring(resp.text)
    r003 = root.find("R003")
    dates = []
    if r003 is not None:
        for rgt_std_dt in r003.iter("RGT_STD_DT"):
            val = rgt_std_dt.get("value")
            if val:
                dates.append(val)
    return dates


def classify_cycle(dates: list[str], cutoff: str) -> str:
    filtered = sorted(d for d in dates if d and d <= cutoff)
    if len(filtered) == 0:
        return "무배당"
    if len(filtered) == 1:
        return "확인불가"
    parsed = [datetime.strptime(d, "%Y%m%d") for d in filtered]
    diffs = [(parsed[i + 1] - parsed[i]).days for i in range(len(parsed) - 1)]
    avg_diff = sum(diffs) / len(diffs)
    if avg_diff <= 45:
        return "월배당"
    elif avg_diff <= 120:
        return "분기배당"
    elif avg_diff <= 270:
        return "반기배당"
    elif avg_diff <= 400:
        return "연배당"
    else:
        return "비정기배당"


def sanity_check():
    test_isin = "KR7471760009"
    dates = fetch_distribution_history(test_isin)
    print(f"[세션 확인] {test_isin} 배당이력: {dates}")
    if len(dates) == 0:
        print("⚠️ 세션이 만료됐거나 인증 실패로 보입니다. .env의 SEIBRO_COOKIES를 갱신하세요.")
        sys.exit(1)
    print("[세션 확인] 정상\n")


def main():
    sanity_check()

    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)")
    master["pd_itm_no"] = master["pd_itm_no"].astype(str).str.strip()

    missing_mask = master[TARGET_COL].apply(is_blank)
    target_idx = master[missing_mask].index
    isins = master.loc[target_idx, "pd_itm_no"].dropna().unique()
    print(f"{TARGET_COL} 결측: {len(target_idx)}건 / 고유 종목 {len(isins)}개")

    isin_to_raw = {}
    for i, isin in enumerate(isins):
        try:
            dates = fetch_distribution_history(isin)
            isin_to_raw[isin] = classify_cycle(dates, CUTOFF_DATE)
        except Exception as e:
            print(f"  [에러] {isin}: {e}")
        if (i + 1) % 200 == 0:
            print(f"  진행: {i+1}/{len(isins)}")
        time.sleep(0.15)

    print(f"\n=== 배당주기 분포(조회 결과) ===\n{pd.Series(isin_to_raw.values()).value_counts()}")

    audit_rows = []
    filled = 0
    for idx in target_idx:
        isin = master.at[idx, "pd_itm_no"]
        raw = isin_to_raw.get(isin)
        code = CYCLE_CODE_MAP.get(raw)
        if code is None:
            continue  # 무배당/확인불가/비정기배당은 채울 코드가 없음
        master.at[idx, TARGET_COL] = code
        filled += 1
        audit_rows.append({
            "pd_itm_no": isin,
            "column": TARGET_COL,
            "filled_value": code,
            "source": "SEIBro ETF종합정보 API (분배금 지급현황 이력 기반)",
            "base_dt": CUTOFF_DATE,
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
