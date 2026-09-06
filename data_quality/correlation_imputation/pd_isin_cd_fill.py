"""
pd_isin_cd_fill.py

목적:
    국내ETF 마스터의 pd_isin_cd(Refinitiv ISIN) 결측치를 pd_itm_no(상품번호) 값을
    그대로 복사해서 채운다.

근거:
    - 국내 시장은 ETF의 "상품번호"로 쓸 수 있는 게 ISIN(표준코드)뿐이라, pd_itm_no가
      이미 그 자체로 ISIN 형식(예: KR7069500007, 12자리)임.
    - 값이 둘 다 있는 1,208건을 전수 비교한 결과 1,207건이 완전히 일치(99.9%),
      유일한 불일치 1건은 pd_itm_no="KR"(사실상 빈 값, 손상된 데이터)이라 애초에
      비교 대상에서 제외해야 하는 예외였음.
    - "국내ETF"라는 이름이 붙어있어도 실제로는 해외지수를 추종하는 상품(641건,
      36%)이 섞여 있는데, 이 서브셋만 따로 검증해도 100% 일치(n=453) -> ISIN은
      "무엇을 추종하는지"가 아니라 "어느 거래소에 상장했는지"로 결정되므로
      투자 대상과 무관하게 안전한 규칙임을 재확인함.

예외 처리:
    - pd_itm_no가 정상 ISIN 형식(KR + 10자리 영숫자, 총 12자리)이 아닌 행은
      복사하지 않고 결측인 채로 남겨둠 (손상된 원본 데이터를 그대로 옮기지 않기 위함).

사용법: python pd_isin_cd_fill.py
"""

import os
import re
import pandas as pd
from datetime import datetime

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref01n001_국내ETF마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내ETF_audit_log.csv"

TARGET_COL = "pd_isin_cd"
SOURCE_COL = "pd_itm_no"
SOURCE_LABEL = "internal_derivation(pd_itm_no 값 그대로 복사, 검증정확도 99.9%)"

ISIN_PATTERN = re.compile(r"^KR[A-Z0-9]{10}$")
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


def main():
    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)\n")

    missing_mask = master[TARGET_COL].apply(is_blank)
    before = missing_mask.sum()
    print(f"{TARGET_COL} 결측: {before}건")

    is_valid_isin_format = master[SOURCE_COL].astype(str).str.match(ISIN_PATTERN, na=False)
    skipped = (missing_mask & ~is_valid_isin_format).sum()
    if skipped > 0:
        print(f"⚠️ pd_itm_no가 정상 ISIN 형식이 아니라 건너뛴 행: {skipped}건")
        print(master.loc[missing_mask & ~is_valid_isin_format, ["pd_itm_no", "pd_nm"]].to_string(index=False))

    to_fill = missing_mask & is_valid_isin_format

    audit_rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filled = 0

    for idx in master.loc[to_fill].index:
        val = master.at[idx, SOURCE_COL]
        master.at[idx, TARGET_COL] = val
        filled += 1
        audit_rows.append({
            "pd_itm_no": val, "column": TARGET_COL,
            "filled_value": val, "source": SOURCE_LABEL,
            "filled_at": now_str,
        })

    after = master[TARGET_COL].apply(is_blank).sum()
    print(f"\n{TARGET_COL} 결측: {before} -> {after} (신규 채움 {filled}건, {filled/before*100:.1f}%)")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"\n저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    if audit_rows:
        append_audit_log(audit_rows, AUDIT_LOG_PATH)
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
