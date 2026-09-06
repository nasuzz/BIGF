# 2번 RAG 담당자 인계 안내

이 문서는 2번 담당자가 질문에서 만든 `RetrievalPlan`을 실제 PostgreSQL 검색과 연결하기 위한 최소 안내입니다.

## 1. 전달 방식

둘 중 하나를 선택합니다.

### 방법 A: 팀 공용 PostgreSQL

호스트·포트·DB명·읽기 전용 계정을 개인 메시지나 비밀 관리 도구로 전달합니다. GitHub와 공개 채널에는 비밀번호를 올리지 않습니다.

### 방법 B: 비공개 백업 복원

백업 파일은 GitHub가 아닌 드라이브 등 제한된 공간으로 전달합니다.

```bash
createdb fund_ontology
pg_restore --no-owner --no-privileges -d fund_ontology fund_ontology.backup
```

복원 환경에는 PostgreSQL과 pgvector가 설치되어 있어야 합니다.

백업 없이 새 빈 DB의 구조만 만들 때는 저장소 루트에서 canonical schema 진입점을
실행합니다. 이미 데이터가 있는 DB에는 이 명령을 실행하지 않습니다.

```bash
psql -X --set=ON_ERROR_STOP=1 -d fund_ontology \
  -f fund-db-handoff/schema.sql
```

기존 NCP DB의 함수·테이블 변경은 `schema.sql` 재실행이 아니라 저장소 루트의
`python scripts/apply_migrations.py`로 적용합니다. 자세한 운영 순서는
`docs/SCHEMA_MANAGEMENT.md`와 `migrations/README.md`를 확인하세요.

## 2. 코드 실행 준비

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` 예시:

```dotenv
PGHOST=localhost
PGPORT=5432
PGDATABASE=fund_ontology
PGUSER=postgres
PGPASSWORD=개인에게_전달받은_값
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_DIMENSION=1024
```

## 3. 인계 데이터 확인

pgAdmin에서 `db/verify_handoff.sql`을 실행합니다. 핵심 확인값은 다음과 같습니다.

- Raw 전체: 53,375행
- Core 전체: 53,374행
- 해외 ETF 전략문: 6,026건
- 임베딩 누락: 0건
- 임베딩 모델: `Qwen/Qwen3-Embedding-0.6B`
- pgvector: 0.8.6
- `data_v1`: DRAFT
- `retrieval_v1`: DRAFT

국내 ETF 1행은 검증 ERROR로 Core에서 제외됐습니다. 경고 행은 삭제하거나 임의 보정하지 않고 `has_warning`과 `audit` 정보로 전달합니다.

## 4. RetrievalPlan 계약

2번 담당자가 DB 쪽에 전달할 필드는 다음 7개입니다.

```json
{
  "intent": "filter_and_explain",
  "dataset": "overseas_etf",
  "filters": {},
  "semantic_query": "인공지능 산업 투자 전략",
  "sort": null,
  "top_k": 5,
  "required_fields": ["product_name", "ticker", "strategy"]
}
```

- `intent`: 질문의 검색 목적
- `dataset`: `public_fund`, `domestic_etf`, `overseas_etf`, `domestic_bond`
- `filters`: 정확한 조건 검색
- `semantic_query`: 해외 ETF 전략문 의미 검색 문장
- `sort`: 정렬 필드와 방향
- `top_k`: 최대 결과 수, 1~100
- `required_fields`: 답변에 반드시 필요한 필드

DB에는 계약과 필드 매핑이 `meta.retrieval_contract`, `meta.retrieval_dataset`, `meta.retrieval_field_mapping`에 등록되어 있습니다. 임의의 SQL이나 임의 컬럼명을 만들지 말고 이 매핑을 기준으로 검색합니다.

## 5. 검색 원칙

- 수치·날짜·범주·정렬·집계: SQL
- 상품명·티커·ISIN: 정확 검색과 별칭 검색
- 해외 ETF 전략·테마: pgvector
- 복합 질문: SQL로 후보를 줄인 뒤 벡터 검색
- 최종 근거: 최대 5개

임베딩 질의도 문서 생성 때와 같은 모델과 정규화 옵션을 사용해야 합니다. 벡터 비교는 cosine distance 연산자 `<=>`를 사용합니다.

```sql
SELECT
    document_id,
    product_id,
    raw_text,
    1 - (embedding <=> %(query_embedding)s::vector) AS score
FROM search.overseas_etf_strategy
WHERE embedding IS NOT NULL
ORDER BY embedding <=> %(query_embedding)s::vector
LIMIT %(top_k)s;
```

`search_pg.py`는 위 흐름을 확인하는 간단한 예제입니다. 서비스 코드에서는 쿼리 문자열을 직접 조합하지 말고 파라미터 바인딩과 허용 필드 목록을 사용하세요.

## 6. Evidence 반환 시 필요한 정보

각 검색 결과는 가능하면 다음 정보를 함께 반환해야 합니다.

- 상품 식별자와 상품명
- 실제 답변에 사용한 필드·값·단위
- `snapshot_id`
- 원본 파일명, 시트명, 행 번호
- 데이터 기준일
- 출처 상태와 경고 여부

`raw_row_id`로 `raw.source_row`에 연결하면 원본 파일·시트·행을 추적할 수 있습니다. `estimated` 값은 최종 사실 근거에서 제외합니다.

## 7. 답변 가능성 코드

상태:

- `answered`
- `partial`
- `needs_clarification`
- `unanswerable`

사유 코드:

- `INVALID_VALUE`
- `UNRESOLVED_ENTITY`
- `AMBIGUOUS_ENTITY`
- `FIELD_NOT_SUPPORTED`
- `TARGET_VALUE_MISSING`
- `OUT_OF_PERIOD`
- `ZERO_MATCH`
- `OUT_OF_SCOPE`
- `ESTIMATED_ONLY`

최종 계약을 바꿀 때는 1·2·3번 담당자가 합의한 뒤 `retrieval_v1`을 갱신하고 회귀 테스트를 수행합니다.
