# PostgreSQL 구조화 검색 조건

`return_1y`와 `one_year_return`은 모두 canonical `return_1y_pct`를 가리킨다.
SQL 필터, 정렬, 반환 근거 및 provenance 정제에서 동일한 값으로 처리한다.

순자산(`net_assets`), 1년 수익률, 보수율(`fee_rate`)은 오름차순과 내림차순을
지원하며 결측값은 양방향 모두 마지막에 둔다. DB `search.filter_products()`의
기존 정렬 계약에 없는 방향은 `search.product_metrics`의 전체 후보에서 조건과
ACTIVE snapshot을 적용하고 정렬한 뒤 LIMIT한다. 함수가 반환한 최대 100건을
반대 방향으로 다시 정렬하는 방식은 사용하지 않는다.

## 매수 가능 여부: A가 확인한 마스터 데이터 계약

`core.v_product_sale_availability`는 마스터 기준일의 상품 단위 매수 후보 여부를
true/false/NULL로 제공한다. 실시간 주문 가능 여부가 아니며 계좌·채널·고객 자격,
장중 거래정지와 주문 시스템 상태는 보장하지 않는다. 응답에도 이 범위를 표시한다.
채권은 기존 전용 검색 계약을 유지한다.

### ETF

- `pd_sale_yn`: 문자열/JSON 숫자 `1`, `1.0`은 판매 대상, `0`, `0.0`은 판매 불가.
- `pd_tr_yn`: `1`, `1.0`은 **거래정지**, `0`, `0.0`은 거래정상.
- 판매 대상 + 거래정상만 true. 명시적 판매 불가 또는 거래정지는 false.
- 빠진 값은 NULL. 예를 들어 판매 불가 + 거래정지 여부 누락은 false,
  판매 대상 + 거래정지 여부 누락은 NULL이다.
- `Y/N`, JSON boolean 등 계약 밖 코드가 있으면 NULL이다. A의 판정표의
  “미확인 코드 + 임의 값 → NULL”을 우선 적용한다. 제안 SQL처럼 코드를
  단순 NULL로 정규화한 뒤 다른 false 조건으로 덮지 않는다.

### 공모펀드

- `prvo_pbff_desc=공모`, `sale_yn=판매중`, `thco_sale_yn=Y`가 모두 확인되면 true.
- 사모 또는 판매완료가 확인되면 false. 판매완료는 청산을 뜻하지 않는다.
- 당사 취급 코드의 빈 값과 `N` 등 미확인 코드는 true로 인정하지 않는다.
- `sale_yn`과 payload의 `sale_status` 또는 실제 `core.fund_class.sale_status`가
  서로 다르면 다른 조건보다 먼저 NULL / SALE_STATUS_CONFLICT로 처리한다.
  원천 판매 상태가 없으면 파생값만으로 채우지 않는다.

### 원본 연결과 출처

`core.product.raw_row_id`로 `raw.source_row.payload`를 연결하며 snapshot_id도
일치해야 한다. 상품군과 원본 데이터셋이 맞지 않으면 미확인으로 처리한다.
원천 코드 PREF01N001/PREF02N001/PRFD01N001과 저장소의 canonical 코드
DOMESTIC_ETP/OVERSEAS_ETP/PUBLIC_FUND를 각 상품군에 맞춰 허용한다.

ETF는 `du_upt_dt`, 펀드는 `fd_daily_bas_dt`를 기준일 원문으로 보존하고, 없으면
스냅샷 기준일을 사용한다. 오래된 스냅샷을 CURRENT_DATE와 비교하지 않는다.
적격하지 않거나 estimated로 표시된 판정 입력값은 매수 후보 판정에 사용하지 않는다.

ACTIVE 버전의 상품만 조회한다. 상품의 출처·기준일과 판정 기준일·범위·사유를
근거에 포함하며, 기존 raw 데이터나 ACTIVE 매핑을 이 migration에서 변경하지 않는다.

### 조회와 결측 커버리지

`eq true`는 IS TRUE, `eq false`와 `ne true`는 IS FALSE로 검색한다.
NULL은 어느 쪽에도 포함하지 않고 COALESCE(false)하지 않는다.

상품군·다른 조건·선행 상품 ID를 적용한 후보 집합에서 매수 상태가 미확인인 건수를
**매수 가능 필터와 LIMIT 적용 전** 집계한다. 참인 상품이 없더라도 통계 행은
보존하므로, 미확인이 남으면 부분/답변 불가로 안내하고 정상 ZERO_MATCH와 구분한다.
기존 상품 정렬은 판정 필터 후 전체 일치 후보에 적용한다.

### 배포 및 확인

1. 기존 DB에서는 `V20260906_005__add_product_sale_availability.sql` 적용 후 API를
   배포한다. 새 DB 초기화 스키마에도 같은 뷰 정의가 포함되어 있다.
2. 마이그레이션 러너 사용 전 수동 적용 이력과 checksum을 대조한다.
   `--target 20260906_005`는 005만 선택하는 옵션이 아니라 그 이전 미적용 변경도
   포함한다. `migrations/README.md` 절차와 dry-run으로 적용 대상을 먼저 확인한다.
3. 앱 DB 사용자의 뷰 조회 권한과 다음 분포를 확인한다.

```sql
SELECT product_type, dataset_code, sale_available, sale_available_reason, count(*)
FROM core.v_product_sale_availability
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;
```

4. 실제 서버에서 “매수 가능한 국내 ETF 3개”, “매수 가능한 해외 ETF 3개”,
   “매수 가능한 공모펀드 3개”를 호출하고 결과의 raw flags·출처·기준일을 대조한다.
   거래정지 상품 제외, NULL 커버리지 및 마스터 범위 안내를 함께 확인한다.

### 격리된 PostgreSQL 검증

Python 회귀 테스트는 실제 pipeline/API에 DB 대역을 연결한다. 별도로 PGlite의
PostgreSQL 엔진에서 canonical 테이블 정의와 migration, gateway가 생성한 SQL을
직접 실행한다. 운영 DB 접속이나 데이터 변경 없이 다음처럼 재현할 수 있다.

```sh
npm install --prefix /tmp/sale-sql-test @electric-sql/pglite@0.5.8
PGLITE_MODULE_PATH=/tmp/sale-sql-test/node_modules/@electric-sql/pglite \
SALE_TEST_PYTHON=.venv/bin/python node tests/test_sale_availability_sql.mjs
```

## 아직 지원하지 않는 조건

논리 스키마에 있어도 PostgreSQL에 구현되지 않은 기간별 수익률 등은 등록된
gateway의 사전 검사로 구분한다. 계획에 사유를 보존하고 해당 단계는 unavailable로
처리하며, 이를 의존하는 단계에는 정상 0건을 전달하지 않는다. 기존 메모리 검색
및 사전 검사를 제공하지 않는 gateway의 계약은 변경하지 않는다.
