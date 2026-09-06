# -*- coding: utf-8 -*-
"""
[소스: Yahoo Finance API (비공식)] 해외ETF 시세 6개 필드 -> 원본 컬럼 결측치만 직접 채움
(pd_curr_cd, du_hpr, du_lpr, du_vol_1d, du_bpr, pd_lstg_dt)

★ 이번 버전에서 고친 것 (중요)
  du_last_nav(최종NAV)를 이 소스로 채우면 안 된다는 게 확인돼서 제거함.
  이유: Yahoo Finance의 'regularMarketPrice'는 "시장가격"이지 "NAV(순자산가치)"가
  아님. ETF는 원래 시장가격과 NAV가 다를 수 있어서(그 차이가 곧 "괴리율"), 시장가격을
  NAV라고 채워넣으면 부정확한 데이터가 됨. 진짜 NAV는 운용사/수탁은행이 공식 계산하는
  값이라 Yahoo 같은 무료 소스에서는 못 구함 (Bloomberg/Refinitiv 등 유료 서비스 필요).
  -> du_last_nav는 이 스크립트에서 완전히 제거. 결측인 채로 두고 "공식 NAV 데이터
     소스 부재로 인한 구조적 결측"으로 문서화할 것.

- 나머지 6개(고가/저가/거래량/기준가/통화/상장일)는 원래 개념 자체가 "시장 거래
  데이터"라 Yahoo가 주는 값과 정확히 일치하므로 그대로 유지
- yahoo_* 새 컬럼을 만들지 않고, 원본 컬럼 각각의 빈 칸만 채움
- 공식 정부/거래소 API 아님. 비공식이지만 널리 쓰이는 소스임을 문서에 명시할 것
- pd_itm_no에 .K/.O 같은 접미사가 붙어있어 야후 조회 전에 제거 필요
- 어떤 행/필드가 채워졌는지는 마스터가 아니라 별도 audit_log.csv에 기록

사용법:
  python 05_yahoo_finance_overseas_etf.py
"""

import os
import re
import time
import requests
import pandas as pd

# ============ 여기를 실제 경로로 수정하세요 ============
ORIGINAL_MASTER_PATH = r"C:\Users\nasuz\Downloads\BIGF\data\pref02n001_해외ETF마스터_data.xlsx"
OUTPUT_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF마스터_filled.xlsx"
AUDIT_LOG_PATH = r"C:\Users\nasuz\Downloads\BIGF\missing_value_analysis\external_api\output\해외ETF_audit_log.csv"

YAHOO_CRAWL_DATE = "20260824"  # 실제 크롤링 실행 날짜로 수정
REQUEST_DELAY_SEC = 0.5  # 야후는 너무 빠르게 치면 일시 차단될 수 있어서 넉넉히 둠

# 야후 응답 필드 -> 원본 마스터 컬럼 매핑 (du_last_nav/'nav' 제거됨 - 위 설명 참고)
FIELD_TO_MASTER_COL = {
    "curr": "pd_curr_cd",
    "high": "du_hpr",
    "low": "du_lpr",
    "vol": "du_vol_1d",
    "base_price": "du_bpr",
    "lstg_dt": "pd_lstg_dt",
}
# ======================================================

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def clean_ticker(raw: str) -> str:
    """pd_itm_no의 .K/.O 같은 접미사를 제거해서 야후용 티커로 변환."""
    return re.sub(r"\.[A-Z]+$", "", raw.strip())


def get_yahoo_nav(ticker: str):
    """Yahoo Finance chart API로 여러 시세 필드를 한 번에 가져온다."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        data = resp.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None, "no_result"
        meta = result[0].get("meta", {})
        # regularMarketPrice는 더 이상 nav로 쓰지 않음. 응답 자체는 여전히 확인해서
        # 이 종목이 유효한지(가격 필드가 존재하는지)만 판단하는 용도로 남겨둠
        price = meta.get("regularMarketPrice")
        if price is None:
            return None, "no_price_field"
        fields = {
            "curr": meta.get("currency"),
            "high": meta.get("regularMarketDayHigh"),
            "low": meta.get("regularMarketDayLow"),
            "vol": meta.get("regularMarketVolume"),
            "base_price": meta.get("chartPreviousClose"),
        }
        if meta.get("firstTradeDate"):
            fields["lstg_dt"] = pd.to_datetime(meta["firstTradeDate"], unit="s").strftime("%Y%m%d")
        return fields, "ok"
    except Exception as e:
        return None, f"error:{e}"


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

    # 조회 대상: 6개 필드 중 "하나라도" 결측인 행 전부 (특정 필드 하나만 기준으로 잡으면
    # 그 필드는 채워져 있는데 다른 필드가 비어있는 행을 놓치게 됨)
    target_cols = list(FIELD_TO_MASTER_COL.values())
    is_missing = master[target_cols].apply(lambda col: col.apply(is_blank)).any(axis=1)
    target_idx = master[is_missing].index
    print(f"6개 필드 중 하나라도 결측인 행: {len(target_idx)}건 처리 시작")

    audit_rows = []
    status_counts = {}
    filled_any = 0

    for i, idx in enumerate(target_idx, 1):
        if i % 200 == 0:
            print(f"  진행 {i}/{len(target_idx)}")
        raw_ticker = master.at[idx, "pd_itm_no"]
        ticker = clean_ticker(raw_ticker)
        fields, status = get_yahoo_nav(ticker)
        status_counts[status] = status_counts.get(status, 0) + 1

        if fields is not None:
            row_filled = False
            for yahoo_key, master_col in FIELD_TO_MASTER_COL.items():
                if yahoo_key not in fields or fields[yahoo_key] is None:
                    continue
                # 그 필드가 실제로 비어있을 때만 채움 (다른 필드는 안 건드림)
                if not is_blank(master.at[idx, master_col]):
                    continue
                master.at[idx, master_col] = fields[yahoo_key]
                row_filled = True
                audit_rows.append({
                    "pd_itm_no": raw_ticker,
                    "column": master_col,
                    "filled_value": fields[yahoo_key],
                    "source": "Yahoo Finance chart API (비공식)",
                    "base_dt": YAHOO_CRAWL_DATE,
                })
            if row_filled:
                filled_any += 1

        time.sleep(REQUEST_DELAY_SEC)

    print("\n=== 조회 상태 분포 ===")
    for status, cnt in status_counts.items():
        print(f"  {status}: {cnt}")
    print(f"\n필드 하나라도 채워진 행: {filled_any}/{len(target_idx)}건")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    master.to_excel(OUTPUT_PATH, index=False)
    print(f"저장 완료 (원본과 동일한 컬럼 구조): {OUTPUT_PATH}")

    append_audit_log(audit_rows, AUDIT_LOG_PATH)
    if audit_rows:
        print(f"감사 로그(출처 추적용) 저장: {AUDIT_LOG_PATH}")

    print("\n✅ du_last_nav는 이 스크립트에서 채우지 않습니다 (Yahoo가 진짜 NAV를 제공하지 않음).")


if __name__ == "__main__":
    main()
