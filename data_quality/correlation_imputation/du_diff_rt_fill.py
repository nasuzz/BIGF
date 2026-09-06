"""
du_diff_rt_fill.py

목적:
    해외ETF 마스터의 du_diff_rt(괴리율) 결측치를 계산해서 채운다.

    괴리율(%) = (종가 - NAV) / NAV × 100

⚠️ 반드시 읽을 것 (지난번 실수 관련)
    du_last_nav는 Yahoo Finance로 채우면 안 된다는 게 확인됐음 (Yahoo가 주는 건
    "시장가격"이지 "공식 NAV"가 아니라서, 그걸로 괴리율을 계산하면 항상 0%에 가깝게
    나오거나 극단치가 섞인 의미 없는 값이 됨).
    -> 이 스크립트는 du_last_nav가 "원본 마스터에 이미 있던 진짜 값"인 행에서만
       계산한다. Yahoo로 채워진 적 없는(즉 원본 그대로인) NAV만 신뢰해서 사용.
    -> 그 결과 전체 결측 6,034건 중 758건만 채워짐(12.6%). 나머지는 애초에 진짜
       NAV 자체가 없어서 계산이 불가능한 것 -> "공식 NAV 데이터 소스 부재로 인한
       구조적 결측"으로 문서화할 것 (du_last_nav와 같은 근본 원인).

사용법: python du_diff_rt_fill.py
"""

import os
import pandas as pd
from datetime import datetime

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref02n001_해외ETF마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF_audit_log.csv"

TARGET_COL = "du_diff_rt"
NAV_COL = "du_last_nav"
PRICE_COL = "du_clpr"
SOURCE_LABEL = "internal_calculation((종가-NAV)/NAV, 원본 NAV만 사용)"
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

    nav = pd.to_numeric(master[NAV_COL], errors="coerce")
    price = pd.to_numeric(master[PRICE_COL], errors="coerce")

    # 계산 가능 조건: du_diff_rt가 결측이고, NAV/종가 둘 다 값이 있고, NAV가 0이 아님
    computable = missing_mask & nav.notna() & price.notna() & (nav != 0)
    print(f"계산 가능한 행(진짜 NAV + 종가 둘 다 있음): {computable.sum()}건")

    audit_rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filled = 0

    for idx in master.loc[computable].index:
        n = nav.loc[idx]
        p = price.loc[idx]
        diff_rt = round((p - n) / n * 100, 4)
        master.at[idx, TARGET_COL] = diff_rt
        filled += 1
        audit_rows.append({
            "pd_itm_no": master.at[idx, "pd_itm_no"], "column": TARGET_COL,
            "filled_value": diff_rt, "source": SOURCE_LABEL,
            "rule": f"nav={n}, clpr={p}", "filled_at": now_str,
        })

    after = master[TARGET_COL].apply(is_blank).sum()
    print(f"\n{TARGET_COL} 결측: {before} -> {after} (신규 채움 {filled}건, {filled/before*100:.1f}%)")
    print(f"\n※ 나머지 {after}건은 진짜(원본) NAV 데이터 자체가 없어서 계산 불가")
    print("  -> du_last_nav와 같은 근본 원인(공식 NAV 데이터 소스 부재)의 구조적 결측")
    print("  으로 문서화 권장. 절대 Yahoo 시장가격을 NAV 대신 써서 채우지 말 것.")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"\n저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    if audit_rows:
        append_audit_log(audit_rows, AUDIT_LOG_PATH)
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
