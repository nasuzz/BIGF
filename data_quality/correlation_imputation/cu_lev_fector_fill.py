"""
cu_lev_fector_fill.py

목적:
    해외ETF 마스터의 cu_lev_fector(추적배수) 결측치 중, 상품명에 명시적으로
    배수가 적힌 것("2X", "-2X", "3X" 등)만 정규식으로 추출해서 채운다.

⚠️ 사전 검증 결과 (중요, 반드시 읽을 것)
    - cu_lev_fector 값이 있는 901건을 전수 확인한 결과, 단 1건도 1.0(일반/비레버리지)이
      없었음 -> 이 필드는 "레버리지/인버스 상품에만 명시적으로 값을 넣고, 일반 상품은
      아예 안 채우는" 구조. 즉 전체 결측 5,136건 대부분은 크롤링/추정으로 채울 대상이
      아니라 "레버리지 상품이 아니라서 원래 값이 없는" 구조적 결측임.
    - 이 스크립트는 그중에서도 "진짜 레버리지 상품인데 이름엔 배수가 적혀있으면서
      값만 빠진" 케이스만 추출해서 채움. known 901건 기준 검증 정확도 99.6%(707건 중
      3건만 불일치 - GraniteShares/T-REX/Tradr 특정 상품 3개, 이름과 반대 부호로
      기재된 예외 케이스로 확인됨).
    - 결측 5,136건 중 이 방식으로 채울 수 있는 건 42건(0.8%)뿐. 나머지는 "일반 상품이라
      원래 값이 없는" 구조적 결측으로 문서화할 것 (억지로 1.0을 채우지 않음 - 그건
      검증된 추출이 아니라 가정이므로).
    - "1x A & 1x B" 형태로 배수가 두 번 나오는 복합자산 상품 2건(ISSB.K, ISBG.K)은
      단일 배수 개념과 안 맞아 제외 (정규식이 첫 번째 "1x"만 잡아버리는 문제 있었음).

정규식 로직:
    1. 상품명에서 "숫자(+소수점)X" 패턴 추출 (예: "2X", "-1.5X", "3X")
    2. SHORT/INVERSE/BEAR/BR 단어가 있으면서 추출된 숫자가 양수면 음수로 전환
       (레버리지 인버스 상품은 실제로 음수로 표기되는 경우가 대부분 - 707건 중
       699건이 이 규칙을 따름. 예외 3건은 알려진 특이 케이스)

사용법: python cu_lev_fector_fill.py
"""

import os
import re
import pandas as pd
from datetime import datetime

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref02n001_해외ETF마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF_audit_log.csv"

TARGET_COL = "cu_lev_fector"
NAME_COL = "pd_nm"
SOURCE_LABEL = "internal_pattern_extraction(상품명 배수표기, 검증정확도 99.6%)"

# 알려진 예외 상품(이름과 실제 부호가 반대인 특이 케이스) - 이 상품들은 이름 패턴으로
# 자동 채우지 않고 건너뜀
KNOWN_EXCEPTION_KEYWORDS = ["T-REX 2X INVERSE DRAM", "GRANITESHARES 2X SHORT SPACEX", "TRADR 2X SHORT META"]

# 복합자산 상품("1x A & 1x B" 형태로 배수가 두 번 이상 나와서 단일 배수 개념이 안 맞는 경우)
# -> ISSB.K/ISBG.K: "1x US Stocks & 1x Bitcoin", "1x Bitcoin & 1x Gold" 식으로
#    정규식이 첫 번째 "1x"만 잡아버려서 오해 소지가 있음. 이 2건은 티커로 직접 제외.
KNOWN_EXCEPTION_TICKERS = ["ISSB.K", "ISBG.K"]
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


def extract_leverage(name: str):
    if pd.isna(name):
        return None
    name_upper = str(name).upper()

    if any(kw in name_upper for kw in KNOWN_EXCEPTION_KEYWORDS):
        return None  # 알려진 예외 케이스는 자동 채움 대상에서 제외

    m = re.search(r"(-?\d+(?:\.\d+)?)\s*X\b", name_upper)
    if not m:
        return None
    val = float(m.group(1))

    has_negative_keyword = bool(re.search(r"\b(SHORT|INVERSE|BEAR|BR)\b", name_upper))
    if has_negative_keyword and val > 0:
        val = -val
    return val


def main():
    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)\n")

    missing_mask = master[TARGET_COL].apply(is_blank)
    before = missing_mask.sum()
    print(f"{TARGET_COL} 결측: {before}건")

    audit_rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filled = 0

    for idx in master.loc[missing_mask].index:
        ticker = master.at[idx, "pd_itm_no"]
        if ticker in KNOWN_EXCEPTION_TICKERS:
            continue  # 복합자산 상품(배수가 두 번 나오는 경우) 제외
        name = master.at[idx, NAME_COL]
        val = extract_leverage(name)
        if val is None:
            continue
        master.at[idx, TARGET_COL] = val
        filled += 1
        audit_rows.append({
            "pd_itm_no": master.at[idx, "pd_itm_no"], "column": TARGET_COL,
            "filled_value": val, "source": SOURCE_LABEL,
            "rule": f"상품명 패턴 추출: {name}", "filled_at": now_str,
        })

    after = master[TARGET_COL].apply(is_blank).sum()
    print(f"\n{TARGET_COL} 결측: {before} -> {after} (신규 채움 {filled}건, {filled/before*100:.1f}%)")
    print(f"\n※ 나머지 {after}건은 대부분 '일반(비레버리지) 상품이라 원래 값이 없는' 구조적")
    print("  결측으로 확인됨 (known 901건 중 1.0=일반상품 표기가 단 1건도 없었음).")
    print("  억지로 1.0을 채우지 않고 결측인 채로 문서화하는 것을 권장.")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"\n저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    if audit_rows:
        append_audit_log(audit_rows, AUDIT_LOG_PATH)
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
