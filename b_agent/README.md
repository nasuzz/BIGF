# B-agent core

외부 추가 데이터가 없어도 질문을 의미 단위로 해석하고, 필요한 검색 경로를 계획하며,
없는 근거를 추측하지 않고 응답 가능성을 판정하는 최소 구현입니다.

## 현재 구현

- 네 상품군 공통 의미 필드와 실제 원본 컬럼 사전
- 한국어 규칙 기반 질문 분석과 상품명·티커·ISIN 분리
- 금액 단위 변환과 신용등급 순서 비교
- `Ontology.ttl`과 동일 registry를 사용하는 테마·관계 grounding
- 단계별 `RetrievalPlan` 생성 및 의존성·필드·통화 검증
- 구조화 검색용 canonical AST (`filters`, `sorts`, `limit`)
- 의존성 없는 BM25 키워드 검색
- 상품 식별, 구조화, 키워드, 벡터, 관계, 편입종목, 문서, 이벤트용 Retriever/`DataGateway` 계약
- 운영 PostgreSQL의 상품 식별·구조화·해외 ETF 키워드·pgvector·기업 관계 검색을 연결하는 `PostgresDataGateway`
- 관계 방향(`subsidiaryOf`)과 상장 자회사 조건, 리서치 의견 문서 조건
- 필드별 모집단·결측 수, 검색 절단 여부, 0건/미적재/오류 구분
- 반환 근거의 필터·관계·편입대상·문서 section·이벤트 날짜 재검증
- 편입종목·기업 관계·문서·최근 이벤트가 없을 때 안전한 판정
- `complete`, `partial`, `unanswerable`, `error` 상태
- API 제출 형식의 다섯 문자열 필드 생성

LLM은 아직 연결하지 않았습니다. 연결하더라도 질문 해석 보조와 검색 근거 문장화만 맡고,
필터·정렬·신용등급 비교·상태 판정은 이 코드에서 결정론적으로 수행합니다.

## 실행

핵심 계획 코드는 Python 3.11 이상에서 실행되며, 운영 PostgreSQL·API·임베딩 검색은
루트 `requirements.txt`의 패키지를 설치해야 합니다.

```bash
python -m b_agent "원화 표시이며 매수 가능한 AA- 이상 채권 5개" --plan-only
python -m b_agent "최근 6개월 우주항공 관련 ETF" --snapshot-date 2026-08-31 --plan-only
python -m unittest discover -v
```

`최근 N개월` 질문에는 반드시 데이터 스냅샷 기준일을 넣습니다. 환경 변수
`MIRAE_SNAPSHOT_DATE=YYYY-MM-DD`로도 지정할 수 있습니다. 기준일이 없으면 임의로
오늘 날짜를 쓰지 않고 계획을 차단합니다.

## A와 연결할 계약

A의 저장소는 `b_agent.gateway.DataGateway`를 구현하면 됩니다. B는 원본 SQL 문자열을
만들지 않고 다음 canonical query를 전달합니다.

```text
product_types: bond/public_fund/domestic_etf/foreign_etf
filters: [{field, operator, value, unit}]
sorts: [{field, direction}]
product_mentions/identifiers/entities/themes/temporal/limit
relation_constraints/document_sections/document_constraints
```

반환 근거에는 최소한 다음 값이 필요합니다.

```text
evidence_id, product_id, product_type, source_id,
content 또는 structured, as_of_date, source_ref, provenance
```

문서 중심 개념 질문은 `product_id` 대신 `concept_id`를 사용할 수 있습니다. 반대로
상품을 고르는 이벤트 검색에는 반드시 `product_id`가 필요합니다. 검색기는 근거와 함께
`coverage_complete`, `coverage_by_field`, `total_hits`, `truncated`를 반환해야 합니다.

`source_ref`에는 원본 파일·시트·행 번호 또는 DB의 추적 가능한 행 키가 들어가야 합니다.
채권의 `pd_no`는 중복될 수 있으므로 영속 행 식별자로 단독 사용하면 안 됩니다.

`sale_available`, 공모펀드 `fee_rate`, 투자 지역은 원본 컬럼 하나를 그대로 복사하는
필드가 아닙니다. A가 컬럼 사전의 파생 규칙과 품질 상태를 적용해 만든 canonical 값을
B에 전달해야 하며, 결측은 `false`나 숫자 0으로 바꾸지 않습니다.

해외 ETF의 현재 순자산은 원 통화 기준이므로 KRW 조건이나 서로 다른 통화 간 순위는
차단합니다. 향후 동일 기준일 환율로 만든 `net_assets_krw`가 A에 추가되면 registry를
갱신해 활성화합니다.

## PostgreSQL 운영 Gateway

API의 기본 파이프라인은 `api.db.connection()`의 공용 connection pool과
`registry_from_gateway(PostgresDataGateway(...))`를 사용합니다. 실제 구현이 있는 경로만
capability snapshot에 등록합니다.

| B capability | PostgreSQL 함수 |
| --- | --- |
| `identity_search` | `search.find_products()` |
| `structured_search` | `search.filter_products()` |
| `keyword_search` | `search.find_overseas_strategies()` |
| `vector_search` | `search.overseas_etf_strategy.embedding <=> query_embedding` |
| `holding_search` | `ext.v_etf_holding`, `ext.v_product_opendart_disclosure` |
| `relation_search` | `search.find_organization_relations()` |

DB 상품군은 `PUBLIC_FUND_CLASS → public_fund`, `DOMESTIC_ETP → domestic_etf`,
`OVERSEAS_ETP → foreign_etf`로 변환합니다. 각 행의 상품 ID·상품군·원본 파일·시트·행·기준일·
snapshot·provenance 상태는 `Evidence`와 `RetrievalBatch`에 보존됩니다.

운영 검색에는 5초 statement timeout을 적용합니다. SQL 실행 실패는 빈 목록으로 바꾸지 않고
예외로 전달하므로 executor가 해당 단계를 `failed`, 답변 정책이 `RETRIEVAL_ERROR`로 구분합니다.
반대로 SQL이 정상 실행되어 0행을 반환한 경우에는 `empty`와 `total_hits=0`으로 처리합니다.

현재 `search.filter_products()`가 제공하지 않는 채권·판매 가능 여부·신용등급·변동성 등의
조건은 임의 SQL이나 추정 값으로 우회하지 않습니다. 벡터 검색은 A의 인계 규격과 동일한
`Qwen/Qwen3-Embedding-0.6B`, 1,024차원, 최대 길이 512, 정규화 옵션을 사용합니다.
질문 임베딩 모델은 API 프로세스에서 한 번만 로드하며, SQL 조건과 선행 상품 후보를 벡터
순위 계산 전에 적용합니다.

기업 관계 원본과 검색 계약은 [`docs/RELATION_SEARCH.md`](../docs/RELATION_SEARCH.md)에
정리되어 있습니다. ACTIVE `ORGANIZATION_RELATION` 스냅샷이 없으면 관계가 없다고
단정하지 않고 미적재 상태로 반환합니다.

## 아직 연결되지 않은 기능

- A의 상태·사유 코드 registry
- 위험 문서, 최근 이벤트 retriever

이 기능들은 기존 parser/planner를 변경하지 않고 retriever registry에 추가하도록 설계했습니다.

## HCX 답변 생성

`CLOVA_API_KEY`, `CLOVA_ENDPOINT`, `MODEL_NAME`이 모두 설정되면 HCX Chat
Completions v3 클라이언트를 답변 생성기에 주입합니다. `HCX_TIMEOUT`은
`REQUEST_TIMEOUT`의 남은 예산을 넘지 않도록 제한하며, 키가 없거나 설정이 잘못된 경우에는
API를 중단하지 않고 결정론적 답변을 사용합니다.

HCX에는 검색 근거와 출처만 전달합니다. `unanswerable`, `error`, `no_match`에서는 HCX를
호출하지 않으며, 생성된 답변의 근거 ID·상품명·수치·날짜·출처를 다시 검사합니다. 인증
실패, 시간 초과, 빈 응답, 잘린 응답 또는 근거 밖 주장이 발견되면 검색 근거 요약으로
안전하게 대체합니다.

## 현재 연결 순서

```text
질문
→ 질문 이해/온톨로지 grounding
→ 상품 식별 또는 관계 검색
→ 편입종목·키워드·이벤트 검색
→ 구조화 조건/정렬
→ 문서 근거
→ 근거 검증·결합
→ complete/partial/unanswerable/error 판정
→ 결정론적 답변 또는 근거 제한 LLM 문장화
```

OT의 에코프로 예시는 `상장 자회사 관계 → 해당 자회사 편입 ETF → 순자산 조건 →
긍정 리서치 문서`로 계획됩니다. 관계와 편입 경로는 운영 Gateway에 연결되어 있고,
관계 원본 스냅샷이나 리서치 데이터가 없으면 실행 결과는 추측이 아니라
`unanswerable`이어야 합니다.
