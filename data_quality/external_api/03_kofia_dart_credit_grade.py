"""
[소스: KOFIA 채권정보센터 API + OpenDART] 국내채권 신용등급 -> 원본 컬럼 crd_grd(+crd_grd_dt) 결측치만 직접 채움

흐름: 발행사명(pd_pbcm) -> OpenDART로 법인등록번호 조회 -> KOFIA API로 신용등급 조회 -> ISIN(pd_no) 매칭

★ 변경사항 (사이드카 -> 직접 채움)
- kofia_crd_grd 같은 새 컬럼을 만들지 않고, crd_grd 자체의 빈 칸만 채움
  (crd_grd_dt도 함께 있으면 KOFIA 조회 기준일로 채움)
- ⚠️ 실제 새 마스터(prbd01n001_국내채권마스터_data.xlsx)를 확인해보니 예전에 있던
  PD_EVCO_CRD_GRD(여러 평가사 등급 나열) 컬럼이 더 이상 존재하지 않습니다.
  채울 자리가 없어서 이번 버전에서는 뺐습니다. (필요하면 팀원과 스키마 재확인 후 별도 처리)
- 등급 선택 정책: 여러 평가사 등급 중 가장 낮은(보수적) 것 유지
- 어떤 행/값이 채워졌는지는 마스터가 아니라 별도 audit_log.csv에 기록

필요 패키지: pip install python-dotenv requests pandas openpyxl OpenDartReader --break-system-packages
"""

import os
import re
import sys
import json
import time
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\prbd01n001_국내채권마스터_data.xlsx"
# 현재 국내채권 마스터를 채우는 스크립트는 이거 하나뿐이지만, 나중에 스크립트가 늘어나도
# 같은 파일에 이어서 누적되도록 동일한 load_master 패턴을 사용
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내채권마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\국내채권_audit_log.csv"
ISSUER_CACHE_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\cache\issuer_to_jurir_cache.json"  # 느린 1단계 재실행 방지용 캐시

KOFIA_CRAWL_DATE = "20260824"  # 실제 크롤링 실행 날짜로 수정

GRADE_COL = "crd_grd"
GRADE_DT_COL = "crd_grd_dt"  # 없으면 None 으로 두면 스킵
# ======================================================

DART_API_KEY = os.getenv("DART_API_KEY")
KOFIA_COOKIES_RAW = os.getenv("KOFIA_COOKIES")

if not DART_API_KEY or not KOFIA_COOKIES_RAW:
    print("⚠️ .env 파일에 DART_API_KEY / KOFIA_COOKIES가 설정되지 않았습니다.")
    sys.exit(1)

KOFIA_URL = "https://www.kofiabond.or.kr/proframeWeb/XMLSERVICES/"
KOFIA_HEADERS = {
    "Accept": "*/*",
    "Content-Type": 'application/x-www-form-urlencoded; charset="UTF-8"',
    "Origin": "https://www.kofiabond.or.kr",
    "Referer": "https://www.kofiabond.or.kr/websquare/control.jsp?w2xPath=/xml/cdttest/BISRnkAnnComp.xml",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
KOFIA_COOKIES = dict(item.split("=", 1) for item in KOFIA_COOKIES_RAW.split("; "))

RATING_SCALE = ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-', 'BBB+', 'BBB', 'BBB-',
                 'BB+', 'BB', 'BB-', 'B+', 'B', 'B-', 'CCC', 'CC', 'C', 'D']
RATING_RANK = {g: i for i, g in enumerate(RATING_SCALE)}


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


def normalize_company_name(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"\(주\)", "", name)
    name = re.sub(r"주식회사", "", name)
    name = re.sub(r"\s+", "", name)
    return name


def build_corp_lookup(dart) -> dict:
    corp_df = dart.corp_codes
    lookup = {}
    for _, row in corp_df.iterrows():
        norm_name = normalize_company_name(row.get("corp_name", ""))
        if norm_name and norm_name not in lookup:
            lookup[norm_name] = str(row.get("corp_code", "")).strip()
    return lookup


def get_jurir_no(dart, corp_code: str) -> str | None:
    try:
        info = dart.company(corp_code)
        if info is not None:
            jurir_no = str(info.get("jurir_no", "")).strip()
            if jurir_no and jurir_no != "nan":
                return jurir_no
    except Exception:
        pass
    return None


def build_kofia_request_xml(jurir_no: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<message>
  <proframeHeader>
    <pfmAppName>BIS-KOFIABOND</pfmAppName>
    <pfmSvcName>BISCdtRnkCmpSrchSO</pfmSvcName>
    <pfmFnName>selectData</pfmFnName>
  </proframeHeader>
  <systemHeader></systemHeader>
<BISCdtRnkCmpDTO>
<schData>{jurir_no}</schData>
<inquiryStd>1</inquiryStd>
<schField>1</schField>
<val10>1</val10>
</BISCdtRnkCmpDTO></message>
'''


def fetch_kofia_ratings(jurir_no: str) -> list[dict]:
    xml_body = build_kofia_request_xml(jurir_no)
    resp = requests.post(KOFIA_URL, headers=KOFIA_HEADERS, cookies=KOFIA_COOKIES,
                          data=xml_body.encode("utf-8"), timeout=15)
    resp.raise_for_status()
    text = resp.text
    root_start = text.find("<root>")
    if root_start == -1:
        return []
    root = ET.fromstring(text[root_start:])

    records = []
    for item in root.iter("BISCdtRnkCmpDTO"):
        isin = (item.findtext("val2") or "").strip()
        agency = (item.findtext("koreanShotNm") or "").strip()
        grade = (item.findtext("creditEstRnkNm") or "").strip()
        std_dt = (item.findtext("standardDt") or "").strip()
        if isin and grade and grade != "-":
            records.append({"isin": isin, "agency": agency, "grade": grade, "standardDt": std_dt})
    return records


def normalize_grade_to_master_format(grade: str) -> str:
    if grade in ("AAA", "D"):
        return grade
    if grade and grade[-1] not in ("+", "-"):
        return grade + "0"
    return grade


def pick_worst_rating(records: list[dict]) -> tuple[str | None, str | None]:
    """여러 평가사 등급 중 가장 낮은(보수적) 것 하나만 선택. (crd_grd, standardDt) 반환"""
    valid = [r for r in records if r["grade"] in RATING_RANK]
    if not valid:
        return None, None
    latest_by_agency = {}
    for r in valid:
        key = r["agency"]
        if key not in latest_by_agency or r["standardDt"] > latest_by_agency[key]["standardDt"]:
            latest_by_agency[key] = r
    worst = max(latest_by_agency.values(), key=lambda r: RATING_RANK[r["grade"]])
    return normalize_grade_to_master_format(worst["grade"]), worst["standardDt"]


def main():
    import OpenDartReader
    dart = OpenDartReader(DART_API_KEY)

    master = load_master(ORIGINAL_MASTER_PATH, OUTPUT_PATH)
    print(f"마스터 로드: {len(master)}행, {len(master.columns)}개 컬럼 (원본 구조 그대로 유지)")
    master["pd_no"] = master["pd_no"].astype(str).str.strip()
    master["pd_pbcm"] = master["pd_pbcm"].astype(str).str.strip()

    grade_missing_mask = master[GRADE_COL].apply(is_blank)
    target = master[grade_missing_mask].copy()
    issuers = target["pd_pbcm"].unique()
    print(f"{GRADE_COL} 결측 {len(target)}건 / 조회 대상 고유 발행사 {len(issuers)}건")

    # 1단계: 발행사명 -> 법인등록번호 (캐시 우선)
    if os.path.exists(ISSUER_CACHE_PATH):
        print("캐시에서 1단계 결과 로드...")
        with open(ISSUER_CACHE_PATH, "r", encoding="utf-8") as f:
            issuer_to_jurir = json.load(f)
    else:
        print("DART 전체 법인목록 로드 중...")
        name_to_corp_code = build_corp_lookup(dart)
        issuer_to_jurir = {}
        for i, name in enumerate(issuers):
            corp_code = name_to_corp_code.get(normalize_company_name(name))
            if corp_code:
                jurir_no = get_jurir_no(dart, corp_code)
                if jurir_no:
                    issuer_to_jurir[name] = jurir_no
            if (i + 1) % 200 == 0:
                print(f"  진행: {i+1}/{len(issuers)}")
            time.sleep(0.05)
        os.makedirs(os.path.dirname(ISSUER_CACHE_PATH), exist_ok=True)
        with open(ISSUER_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(issuer_to_jurir, f, ensure_ascii=False)

    print(f"법인등록번호 확보: {len(issuer_to_jurir)}/{len(issuers)}건")

    # 2단계: KOFIA 조회 -> ISIN별 (등급, 기준일)
    isin_to_result = {}
    unique_jurir = list(set(issuer_to_jurir.values()))
    for i, jurir_no in enumerate(unique_jurir):
        try:
            records = fetch_kofia_ratings(jurir_no)
            by_isin = {}
            for r in records:
                by_isin.setdefault(r["isin"], []).append(r)
            for isin, recs in by_isin.items():
                grade, std_dt = pick_worst_rating(recs)
                if grade:
                    isin_to_result[isin] = (grade, std_dt)
        except Exception as e:
            print(f"  KOFIA 조회 실패 (jurir_no={jurir_no}): {e}")
        if (i + 1) % 50 == 0:
            print(f"  KOFIA 조회 진행: {i+1}/{len(unique_jurir)}")
        time.sleep(0.2)

    # 3단계: 결측 행에 직접 채움 (이미 값 있는 셀은 건드리지 않음)
    audit_rows = []
    filled = 0
    for idx in target.index:
        isin = master.at[idx, "pd_no"]
        result = isin_to_result.get(isin)
        if result is None:
            continue
        grade, std_dt = result
        master.at[idx, GRADE_COL] = grade
        if GRADE_DT_COL in master.columns and is_blank(master.at[idx, GRADE_DT_COL]):
            master.at[idx, GRADE_DT_COL] = std_dt
        filled += 1
        audit_rows.append({
            "pd_no": isin,
            "column": GRADE_COL,
            "filled_value": grade,
            "source": "KOFIA 채권정보센터 API (최저등급 정책)",
            "base_dt": std_dt or KOFIA_CRAWL_DATE,
        })

    print(f"\n{GRADE_COL} 신규 채움: {filled}/{len(target)}건")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    append_audit_log(audit_rows, AUDIT_LOG_PATH)
    if audit_rows:
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")


if __name__ == "__main__":
    main()
