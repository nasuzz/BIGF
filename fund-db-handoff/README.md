# 미래에셋 금융상품 DB·검색 기반

공모펀드, 국내 ETF, 해외 ETF, 국내채권 데이터를 PostgreSQL에 적재하고 검색할 수 있도록 만든 DB 구축 코드입니다. 해외 ETF 전략문은 `Qwen/Qwen3-Embedding-0.6B`로 임베딩해 pgvector에 저장합니다.

## 현재 구성

- `raw`: 원본 Excel 행과 파일·시트·행 번호 보존
- `core`: 날짜·숫자·범주를 정리한 서비스용 테이블
- `search`: 상품 별칭, 키워드 검색, 해외 ETF 전략문 및 임베딩
- `meta`: 스냅샷, 데이터 버전, RetrievalPlan 필드 계약
- `audit`: 결측·중복·형식 오류 및 경고 기록

현재 기준 예상 행 수:

| 데이터 | Raw | Core |
|---|---:|---:|
| 공모펀드 | 23,676 | 23,676 |
| 국내 ETF | 1,780 | 1,779 |
| 해외 ETF | 6,037 | 6,037 |
| 국내채권 | 21,882 | 21,882 |
| 합계 | 53,375 | 53,374 |

해외 ETF 전략문은 6,026건이며 전부 1,024차원 임베딩이 저장되어 있어야 합니다. `data_v1`과 `retrieval_v1`은 팀 합의 전까지 `DRAFT`로 유지합니다.

## 저장소에 포함되는 것

- DB 구조: `schema.sql` (중복 DDL 없이 저장소의 `init/01_schema.sql`을 직접 참조)
- Excel 적재 코드: `load_excel.py`, `load_bond.py`
- 임베딩 생성 코드: `embed_overseas.py`
- 간단한 벡터 검색 예제: `search_pg.py`
- 인계 검증 SQL: `db/verify_handoff.sql`
- 2번 담당자용 안내: `docs/B_HANDOFF.md`

Excel 원본, DB 백업, 실제 임베딩 값, 비밀번호는 GitHub에 올리지 않습니다. 새 빈 DB의
구조는 저장소 루트에서 `psql -f fund-db-handoff/schema.sql`로 만들 수 있습니다. 이
파일은 canonical schema를 상대 경로로 불러오므로 `fund-db-handoff/` 폴더만 따로 떼어
복사하면 안 됩니다. 단독 전달 파일이 필요하면 다음처럼 생성합니다.

```bash
python scripts/schema_artifacts.py --output build/handoff-schema.sql

# 대상 환경의 모든 앱 스키마 객체 owner도 지정해야 할 때만 생성 단계에서 주입
python scripts/schema_artifacts.py \
  --owner ncp_app \
  --output build/handoff-schema.sql
```

생성물과 실제 데이터는 커밋하지 않습니다. 실제 53,375행을 받으려면 비공개로 전달한
백업 파일을 복원하거나 팀 공용 PostgreSQL에 접속해야 합니다.

## 로컬 실행 준비

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env`에 본인의 DB 접속 정보를 넣습니다. 이 파일은 Git에서 제외됩니다.

```bash
python fund-db-handoff/embed_overseas.py --status
python fund-db-handoff/embed_overseas.py
python fund-db-handoff/search_pg.py "large cap growth"
```

`embed_overseas.py`와 `search_pg.py`는 `b_agent`의 query embedder와 동일한
`EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `EMBEDDING_MAX_SEQ_LENGTH` 설정을
사용합니다. 현재 모델과 다른 `embedding_model`이 기록된 행은 embedding이 이미
있더라도 모두 다시 생성하며, 수동 검색도 현재 모델로 생성된 행만 대상으로 합니다.
`--status`는 현재 모델과 일치하는 건수, NULL 건수,
모델 불일치 건수를 DB 변경 없이 보여줍니다. DB 컬럼이 `vector(1024)`로 고정되어
있으므로 `EMBEDDING_DIMENSION`이 1024가 아니면 DB에 연결하기 전에 중단됩니다.

자세한 DB 복원 및 2번 담당 연동 방법은 [`docs/B_HANDOFF.md`](docs/B_HANDOFF.md)를 확인하세요.

## 보안 원칙

- 비밀번호·API 키를 코드나 노트북에 직접 적지 않습니다.
- `.env`, Excel, Parquet, DB 백업은 커밋하지 않습니다.
- 기존 노트북에 평문 키가 있었다면 공유 전에 키를 폐기하고 새 키를 환경변수로 등록합니다.
