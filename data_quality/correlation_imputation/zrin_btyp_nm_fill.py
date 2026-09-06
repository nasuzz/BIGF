"""
02_zrin_btyp_nm_fill.py  (기존 02_zrin_btyp_nm_lookup.py + 03_traintest_validation.py 통합)

목적:
    공모펀드 마스터의 zrin_btyp_nm(제로인 유형명) 결측치를,
    or_attr_desc(운용속성)+ovrs_fd_desc(국내/해외) 조합의 최빈값으로 채운다.

⭐ 이번 버전에서 바뀐 점 ⭐
1. 검증(구 03번)과 채우기(구 02번)를 한 스크립트로 통합
   -> 1단계: train/test 7:3 hold-out 검증으로 규칙이 과최적화 아닌지 먼저 확인
   -> 2단계: 검증을 통과했으면, "전체 데이터"로 다시 규칙을 만들어 실제 결측을 채움
2. 사이드카 파일 대신, 원본 마스터의 zrin_btyp_nm 컬럼에 "결측인 것만" 직접 채움
   (누적 결과 파일이 있으면 이어서 사용 - 01/03/04/06/07번 스크립트와 동일한 방식)
3. purity 기준은 원안 그대로 95% 유지 (n>=100)
4. ⭐ 감사로그 경로를 external_api/output 폴더로 통일 (06/07번 스크립트와 같은 위치 -
   이전엔 correlation_imputation/cache 폴더로 따로 빠져서 로그가 두 곳에 나뉘어 있었음)
5. ⭐ 검증 결과는 별도 엑셀 파일로 안 남기고 콘솔 출력으로만 확인 (실행할 때마다
   터미널에 뜨는 로그 자체가 검증 근거 - 필요하면 그 로그를 캡처해서 문서에 첨부)
5. ⭐ 감사로그 컬럼명도 "field" -> "column"으로 통일 (07번/복구스크립트와 동일 키)

주의:
    - MMF+국내 조합처럼 purity가 95% 근처인 경우 train/test 분리에 따라 통과/탈락이
      바뀔 수 있음 -> 아래 hold-out 검증 결과에서 gap(train-test)이 큰 조합은 특히 주의
"""

import os
import pandas as pd
from sklearn.model_selection import train_test_split

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\prfd01n001_공모펀드마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\공모펀드마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\공모펀드_audit_log.csv"

MIN_PURITY = 0.95
MIN_N = 100
TARGET_COL = "zrin_btyp_nm"
KEY_COLS = ["or_attr_desc", "ovrs_fd_desc"]
TEST_SIZE = 0.3
RANDOM_STATE = 42
SOURCE_LABEL = "internal_correlation_lookup(or_attr_desc+ovrs_fd_desc)"
# ======================================================


def sentinel_mask(s: pd.Series) -> pd.Series:
    """NaN / 공백문자열 / 전부-0 / KR0...0 패턴을 결측으로 판정"""
    if s.dtype in ("float64", "int64"):
        return s.isna()
    st = s.astype(str)
    stripped = st.str.strip()
    return (
        s.isna()
        | ((stripped == "") & ~s.isna())
        | stripped.str.match(r"^0+$", na=False)
        | stripped.str.match(r"^KR0+$", na=False)
    )


def build_lookup(df: pd.DataFrame) -> pd.DataFrame:
    grp = df.groupby(KEY_COLS)[TARGET_COL]
    mode_val = grp.agg(lambda x: x.value_counts().idxmax())
    purity = grp.apply(lambda x: x.value_counts(normalize=True).iloc[0])
    n = grp.size()
    return pd.DataFrame({"mode": mode_val, "purity": purity, "n": n}).reset_index()


def step1_validate(present: pd.DataFrame):
    """구 03번: hold-out 검증. 규칙을 실제로 적용하기 전에 신뢰할 만한지 확인만 함."""
    print("=" * 60)
    print("[1단계] Hold-out 검증 (train 7 : test 3)")
    print("=" * 60)

    train, test = train_test_split(
        present, test_size=TEST_SIZE, random_state=RANDOM_STATE,
        stratify=present["or_attr_desc"]
    )
    print(f"present(값 있는 행): {len(present)}건  ->  train: {len(train)}건 / test: {len(test)}건")

    lookup_train = build_lookup(train)
    qualified_train = lookup_train[(lookup_train["purity"] >= MIN_PURITY) & (lookup_train["n"] >= MIN_N)]
    print(f"\ntrain 기준 채택 조합 ({MIN_PURITY*100:.0f}%+, n>={MIN_N}): {len(qualified_train)}개")

    key_map = {(r["or_attr_desc"], r["ovrs_fd_desc"]): r["mode"] for _, r in qualified_train.iterrows()}

    test = test.copy()
    test["key"] = list(zip(test["or_attr_desc"], test["ovrs_fd_desc"]))
    test["predicted"] = test["key"].map(key_map)
    covered = test["predicted"].notna()
    correct = test.loc[covered, "predicted"] == test.loc[covered, TARGET_COL]

    print(f"\ntest 커버율: {covered.sum()}/{len(test)} ({covered.mean()*100:.1f}%)")
    print(f"test 실측 정확도: {correct.mean()*100:.1f}%")

    rows = []
    for (a, b), mode in key_map.items():
        sub = test[test["key"] == (a, b)]
        if len(sub) == 0:
            continue
        acc = (sub[TARGET_COL] == mode).mean()
        tr = lookup_train[(lookup_train["or_attr_desc"] == a) & (lookup_train["ovrs_fd_desc"] == b)].iloc[0]
        gap = tr["purity"] - acc
        rows.append({
            "or_attr_desc": a, "ovrs_fd_desc": b, "mode": mode,
            "train_purity": tr["purity"], "n_train": tr["n"],
            "test_accuracy": acc, "n_test": len(sub), "gap(train-test)": gap,
        })
        flag = "  ⚠️ gap 큼, 재검토 권장" if abs(gap) >= 0.03 else ""
        print(f"  {a}+{b}: train_purity={tr['purity']*100:.1f}%(n={tr['n']}) "
              f"-> test_accuracy={acc*100:.1f}%(n={len(sub)}){flag}")

    report = pd.DataFrame(rows)
    print(f"\n검증 결과 요약: {len(report)}개 조합 검증 완료 (아래 출력이 곧 검증 근거)")

    if len(report) > 0 and (report["gap(train-test)"].abs() >= 0.03).any():
        print("\n⚠️⚠️ 일부 조합에서 train-test 차이가 3%p 이상입니다. 2단계 진행 전에")
        print("    위 출력을 검토하고, 필요하면 해당 조합을 QUALIFY_EXCLUDE에 추가하세요.")

    return report


def step2_fill(master: pd.DataFrame, present: pd.DataFrame, audit_rows: list, now_str: str):
    """구 02번: 검증 통과 방법론으로, 이번엔 '전체' present 데이터로 규칙을 다시 만들어 채움."""
    print("\n" + "=" * 60)
    print("[2단계] 전체 데이터 기준 규칙 생성 및 실제 채우기")
    print("=" * 60)

    lookup_full = build_lookup(present)
    qualified_full = lookup_full[(lookup_full["purity"] >= MIN_PURITY) & (lookup_full["n"] >= MIN_N)]
    print(f"전체 데이터 기준 채택 조합: {len(qualified_full)}개")
    print(qualified_full.to_string(index=False))

    key_map = {(r["or_attr_desc"], r["ovrs_fd_desc"]): r["mode"] for _, r in qualified_full.iterrows()}

    target_missing = sentinel_mask(master[TARGET_COL])
    before = target_missing.sum()

    filled_count = 0
    for i in master.loc[target_missing].index:
        key = (master.at[i, "or_attr_desc"], master.at[i, "ovrs_fd_desc"])
        val = key_map.get(key)
        if val is not None:
            master.at[i, TARGET_COL] = val
            filled_count += 1
            audit_rows.append({
                "itm_no": master.at[i, "itm_no"], "column": TARGET_COL,
                "filled_value": val, "source": SOURCE_LABEL,
                "rule": f"{key[0]}+{key[1]}", "filled_at": now_str,
            })

    after = sentinel_mask(master[TARGET_COL]).sum()
    print(f"\n{TARGET_COL} 결측: {before} -> {after} (신규 채움 {filled_count}건, "
          f"{filled_count/before*100:.1f}%)")

    return master


def main():
    if os.path.exists(OUTPUT_PATH):
        master = pd.read_excel(OUTPUT_PATH)
        print(f"기존 누적 결과 파일 발견, 이어서 사용: {OUTPUT_PATH}")
    else:
        master = pd.read_excel(ORIGINAL_MASTER_PATH, sheet_name="data")
        print(f"누적 결과 파일 없음, 원본 마스터에서 새로 시작: {ORIGINAL_MASTER_PATH}")

    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)\n")

    target_missing = sentinel_mask(master[TARGET_COL])
    present = master[~target_missing].copy()
    print(f"[{TARGET_COL}] 전체 결측 {target_missing.sum()}건 "
          f"({target_missing.sum()/len(master)*100:.1f}%), present {len(present)}건\n")

    # 1단계: 검증 (파일 저장은 하지만 마스터는 안 건드림)
    step1_validate(present)

    # 2단계: 전체 데이터로 다시 규칙 생성 + 실제 채우기
    from datetime import datetime
    audit_rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    master = step2_fill(master, present, audit_rows, now_str)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"\n저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    if audit_rows:
        audit_df = pd.DataFrame(audit_rows)
        if os.path.exists(AUDIT_LOG_PATH):
            prev = pd.read_csv(AUDIT_LOG_PATH)
            audit_df = pd.concat([prev, audit_df], ignore_index=True)
        audit_df.to_csv(AUDIT_LOG_PATH, index=False, encoding="utf-8-sig")
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
