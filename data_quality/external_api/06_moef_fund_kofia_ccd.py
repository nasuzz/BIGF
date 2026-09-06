# -*- coding: utf-8 -*-
"""
[소스: 금융위원회_펀드상품기본정보 API (data.go.kr)] 공모펀드 원본 컬럼 kofia_fd_ccd, std_itm_no 결측치만 직접 채움

★ 변경사항 (사이드카 -> 직접 채움)
- moef_kofia_fd_ccd / moef_std_itm_no / moef_fill_status 같은 새 컬럼을 만들지 않음
- kofia_fd_ccd(현재 '00000000000000000000' 같은 0-sentinel)와
  std_itm_no(현재 공백 padding sentinel)를 각각 실제로 비어있을 때만 직접 채움
- 처리 상태(matched_unique / not_found / rate_limited 등)는 마스터가 아니라
  캐시 JSON 안에만 유지 (resume 로직에 필요하기 때문. 마스터 컬럼으로는 안 나감)
- 채워진 값 자체는 별도 audit_log.csv에 기록

⭐ 이어하기(resume) 기능 그대로 유지 ⭐
- data.go.kr API는 보통 하루 호출 한도(기본 1,000회)가 있어서, 한 번에 다 못 돔
- 캐시 파일(NAME_CACHE_PATH)에 지금까지 처리한 결과를 계속 누적 저장
- 재실행하면 "이미 처리된 이름"은 건너뛰고, "아직 처리 안 됐거나 지난번에 한도초과로
  실패한 이름"만 이어서 호출
- 한도 초과(rate limit) 재감지 시 즉시 멈추고 캐시 저장 후 "내일 다시 실행하세요" 안내

사용법:
  1) .env 파일에 MOEF_FUND_API_KEY 설정
  2) python 06_moef_fund_kofia_ccd.py   (매일 반복 실행)
"""

import os
import re
import sys
import json
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\prfd01n001_공모펀드마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\공모펀드마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\공모펀드_audit_log.csv"
NAME_CACHE_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\cache\06_moef_name_cache.json"  # 이어하기용 캐시 (마스터와 무관, 계속 유지)

MOEF_CRAWL_DATE = "20260824"  # 실제 크롤링 실행 날짜로 수정 (매일 갱신 권장)
REQUEST_DELAY_SEC = 0.15
SAVE_EVERY = 50

CCD_COL = "kofia_fd_ccd"
STD_CD_COL = "std_itm_no"
# ======================================================

SERVICE_KEY = os.getenv("MOEF_FUND_API_KEY")
if not SERVICE_KEY:
    print("⚠️ .env 파일에 MOEF_FUND_API_KEY가 설정되지 않았습니다.")
    sys.exit(1)

BASE_URL = "https://apis.data.go.kr/1160100/service/GetFundProductInfoService/getStandardCodeInfo"

RETRYABLE_STATUSES = {"rate_limited", "api_error"}


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    cleaned = re.sub(r"[\[\]\(\)（）\s_\-]", "", name)
    return cleaned.strip()


def load_cache() -> dict:
    if os.path.exists(NAME_CACHE_PATH):
        with open(NAME_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    os.makedirs(os.path.dirname(NAME_CACHE_PATH), exist_ok=True)
    with open(NAME_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def call_api(fnd_nm: str, num_of_rows: int = 100) -> dict:
    """펀드명으로 API 조회. 앞부분 10자만 검색어로 사용."""
    search_key = fnd_nm[:10] if len(fnd_nm) > 10 else fnd_nm
    params = {
        "serviceKey": SERVICE_KEY,
        "numOfRows": num_of_rows,
        "pageNo": 1,
        "resultType": "json",
        "likeFndNm": search_key,
    }
    try:
        all_items = []
        page = 1
        max_pages = 5
        total_count = None
        while page <= max_pages:
            params["pageNo"] = page
            resp = requests.get(BASE_URL, params=params, timeout=10)

            if resp.status_code == 429:
                return {"status": "rate_limited", "items": []}
            try:
                probe = resp.json()
                err_msg = probe.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader", {}).get("errMsg", "")
                if "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in err_msg:
                    return {"status": "rate_limited", "items": []}
            except Exception:
                pass

            resp.raise_for_status()
            data = resp.json()

            result_code = data.get("response", {}).get("header", {}).get("resultCode")
            if result_code != "00":
                return {"status": "api_result_error", "items": [],
                        "error": data.get("response", {}).get("header", {}).get("resultMsg")}

            body = data.get("response", {}).get("body", {})
            if total_count is None:
                total_count = body.get("totalCount", 0)

            items = body.get("items", {})
            if isinstance(items, dict):
                item_list = items.get("item", [])
                if isinstance(item_list, dict):
                    item_list = [item_list]
                all_items.extend(item_list)

            if total_count is None or len(all_items) >= total_count or len(all_items) == 0:
                break
            page += 1

        return {"status": "ok", "items": all_items}
    except Exception as e:
        return {"status": "api_error", "items": [], "error": str(e)}


def resolve(name: str, result: dict):
    items = result.get("items", [])
    status_in = result.get("status")
    if status_in in ("rate_limited", "api_error", "api_result_error"):
        return None, None, status_in
    if len(items) == 0:
        return None, None, "not_found"

    target_norm = normalize_name(name)
    exact = [it for it in items if normalize_name(it.get("fndNm", "")) == target_norm]
    if len(exact) == 1:
        return exact[0].get("prdClsfCd"), exact[0].get("asoStdCd"), "matched_unique"
    if len(exact) == 0:
        return None, None, "not_found_exact_name"
    return None, None, "ambiguous_multi_match"


def is_blank(v) -> bool:
    if pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def is_ccd_placeholder(v) -> bool:
    return bool(re.match(r"^0+$", str(v).strip()))


def load_master(original_path: str, output_path: str) -> pd.DataFrame:
    """출력 파일이 이미 있으면(이전 실행에서 누적된 것) 그걸 이어서 읽고,
    없으면 원본 마스터에서 새로 시작한다."""
    if os.path.exists(output_path):
        print(f"기존 누적 결과 파일 발견, 이어서 사용: {output_path}")
        return pd.read_excel(output_path, dtype=str, keep_default_na=False, na_values=[])
    print(f"누적 결과 파일 없음, 원본 마스터에서 새로 시작: {original_path}")
    return pd.read_excel(original_path, dtype=str, keep_default_na=False, na_values=[])


def append_audit_log(audit_rows: list[dict], audit_log_path: str):
    """audit_log.csv를 덮어쓰지 않고 이어붙인다."""
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

    is_placeholder = master[CCD_COL].apply(is_ccd_placeholder)
    target = master[is_placeholder].copy()
    unique_names = target["itm_nm"].dropna().unique().tolist()
    print(f"{CCD_COL} 결측 {len(target)}건 / 고유 펀드명 {len(unique_names)}개")

    cache = load_cache()
    already_done = sum(1 for n in unique_names if n in cache and cache[n]["status"] not in RETRYABLE_STATUSES)
    print(f"캐시에 이미 처리된(재시도 불필요) 이름: {already_done}개")

    to_process = [n for n in unique_names if n not in cache or cache[n]["status"] in RETRYABLE_STATUSES]
    print(f"이번에 호출할 이름: {len(to_process)}개\n")

    stopped_early = False
    for i, name in enumerate(to_process, 1):
        result = call_api(name)

        if result["status"] == "rate_limited":
            print(f"\n⚠️ 일일 호출 한도 초과 감지 (처리 {i-1}/{len(to_process)}건 진행 후 중단)")
            print("   -> 지금까지 결과는 캐시에 저장했습니다. 내일 다시 실행하면 이어서 진행됩니다.")
            stopped_early = True
            break

        ccd, std_cd, status = resolve(name, result)
        cache[name] = {"status": status, "ccd": ccd, "std_cd": std_cd}

        if i % 50 == 0:
            print(f"  진행 {i}/{len(to_process)}")
        if i % SAVE_EVERY == 0:
            save_cache(cache)

        time.sleep(REQUEST_DELAY_SEC)

    save_cache(cache)

    # ---------- 캐시 기반으로 원본 컬럼 직접 채움 (이미 값 있으면 안 건드림) ----------
    audit_rows = []
    filled_ccd = 0
    filled_std = 0
    for idx in target.index:
        name = master.at[idx, "itm_nm"]
        entry = cache.get(name)
        if entry is None:
            continue
        if entry.get("ccd") and is_ccd_placeholder(master.at[idx, CCD_COL]):
            master.at[idx, CCD_COL] = entry["ccd"]
            filled_ccd += 1
            audit_rows.append({
                "itm_nm": name, "column": CCD_COL, "filled_value": entry["ccd"],
                "source": "금융위원회 펀드상품기본정보 API (data.go.kr)", "base_dt": MOEF_CRAWL_DATE,
            })
        if entry.get("std_cd") and STD_CD_COL in master.columns and is_blank(master.at[idx, STD_CD_COL]):
            master.at[idx, STD_CD_COL] = entry["std_cd"]
            filled_std += 1
            audit_rows.append({
                "itm_nm": name, "column": STD_CD_COL, "filled_value": entry["std_cd"],
                "source": "금융위원회 펀드상품기본정보 API (data.go.kr)", "base_dt": MOEF_CRAWL_DATE,
            })

    print(f"\n{CCD_COL} 신규 채움: {filled_ccd}건")
    print(f"{STD_CD_COL} 신규 채움: {filled_std}건")

    status_counts = pd.Series([cache[n]["status"] for n in unique_names if n in cache]).value_counts()
    print("\n=== 누적 캐시 상태 분포(고유 펀드명 기준) ===")
    print(status_counts)
    remaining = status_counts.get("rate_limited", 0) + status_counts.get("api_error", 0) \
        + (len(unique_names) - sum(1 for n in unique_names if n in cache))
    if remaining > 0:
        print(f"\n남은 재시도 대상: {remaining}건 -> 내일 다시 실행하면 이어서 처리됩니다.")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    append_audit_log(audit_rows, AUDIT_LOG_PATH)
    if audit_rows:
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")

    if stopped_early:
        print("\n(참고) 이번 실행은 한도 초과로 중간에 멈췄습니다. 내일 같은 명령으로 다시 실행하세요.")


if __name__ == "__main__":
    main()
