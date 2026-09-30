# BIGF

미래에셋증권 제10회 AI페스티벌 **팀 BIGF** 프로젝트입니다.

펀드·ETF·채권 등 금융상품 데이터를 대상으로, 자연어 질문에 **근거와 함께 답변**하는 RAG 기반 질의응답 API입니다.
"미국 시장에 투자하고 보수가 0.5% 이하인 AI 테마 ETF 알려줘" 같은 질문을 받으면 조건 검색과 의미 검색을 함께 수행하고, 출처가 확인된 데이터만으로 답변합니다.

## 핵심 아이디어

- **근거 기반 답변**: 답변마다 상품명·필드·값·기준일·원본 파일/시트/행을 `[E1]` 형태로 함께 반환
- **환각 방지**: 근거가 없거나 추정값뿐이면 상품을 만들어내지 않고 답변불가·부분답변·재질문으로 처리
- **하이브리드 검색**: 정형 조건(보수, 수익률, 순자산 등) + 키워드/유사도 + 임베딩 의미 검색
- **데이터 버전 관리**: 원본 스냅샷과 값 채움 이력을 추적하고, ACTIVE 버전 데이터만 검색

## 처리 흐름

```
질문 → 질문 분석·검색 계획 → DB 검색(조건/키워드/의미) → 근거 검증 → LLM 답변 생성 → 응답
```

## 기술 스택

Python 3.11 · FastAPI · PostgreSQL 18 (pgvector, pg_trgm) · CLOVA Studio HCX-005 · Docker

## API

평가용 엔드포인트 (인증 불필요)

-

응답(JSON)은 항상 아래 5개 문자열 필드를 가집니다. ([`contracts/external_api.py`](contracts/external_api.py))

| 필드 | 설명 |
| --- | --- |
| `question_id`, `question` | 요청값 그대로 반환 |
| `retrieved_context` | 답변에 사용한 근거 (`[E1] 상품명 / 필드 / 값 / 기준일 / 출처`) |
| `think_trace` | 질문 분석·검색·검증 과정 요약 |
| `answer` | 최종 답변 |

서버 상태 확인은 `GET /health`.

## 실행

### 준비물

- Docker, Docker Compose
- CLOVA Studio API 키 (답변 생성에 사용)

### 1. 환경 변수 설정

```bash
cp .env.example .env
```

`.env`를 열어 `CLOVA_API_KEY`를 채웁니다. 나머지는 기본값으로 바로 실행할 수 있습니다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `CLOVA_API_KEY` | (비어 있음) | CLOVA Studio API 키 (필수) |
| `MODEL_NAME` | `HCX-005` | 답변 생성 LLM |
| `DATA_VERSION` | `data_v1` | 서비스가 사용하는 데이터 버전 |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | 의미 검색용 임베딩 모델 |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `product_finder` / `user` / `password` | DB 초기 계정 (`DATABASE_URL`과 같아야 함) |

### 2. 서버 실행

```bash
docker compose up -d --build
```

- `api` 컨테이너: `8000` 포트로 API 실행, 헬스체크 실패 시 자동 재시작
- `db` 컨테이너: PostgreSQL 18 + pgvector, 외부 포트는 열지 않고 `api`와 내부 네트워크로만 통신
- 처음 실행하면 `init/`의 SQL로 스키마가 자동 생성됩니다. (이미 만들어진 DB 볼륨에는 다시 실행되지 않음)
- GPU가 없는 서버를 기준으로 PyTorch CPU 버전을 설치하므로 첫 빌드는 시간이 걸릴 수 있습니다.

### 3. 동작 확인

```bash
# 서버 상태
curl http://localhost:8000/health

# 질문 보내기
curl -G "http://localhost:8000/answer" \
  --data-urlencode "question_id=test-1" \
  --data-urlencode "question=보수가 낮은 미국 ETF 알려줘"
```

`/health`에서 `database: connected`, `llm: ready`가 나오면 정상입니다.

### 4. 로그 확인 / 종료

```bash
docker compose logs -f api   # API 로그
docker compose down          # 종료 (DB 데이터는 유지)
docker compose down -v       # 종료 + DB 데이터 삭제
```

> 상품 데이터 적재는 별도 작업입니다. DB에 ACTIVE 데이터 버전이 없으면 검색 결과가 비어 있을 수 있습니다.

### Docker 없이 로컬 실행

PostgreSQL(pgvector 포함)을 따로 띄우고 `.env`의 `DATABASE_URL`을 맞춘 뒤 실행합니다.

```bash
python -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

## 저장소 구조

```
api/          FastAPI 서버 (/answer, /health)
agent/        API와 에이전트 연결
b_agent/      질의 계획, 근거 라우팅, HCX 호출
contracts/    API·에이전트 간 데이터 계약
init/         DB 초기화 SQL (canonical 스키마)
migrations/   운영 DB용 버전형 migration
ontology/     금융 도메인 온톨로지
data_quality/ 데이터 품질 검증
scripts/      스키마·migration 운영 스크립트
docs/         설계·운영 문서
tests/        테스트
```

## 문서

- [DB 스키마 관리와 배포](docs/SCHEMA_MANAGEMENT.md)
- [기업 관계 검색 계약](docs/RELATION_SEARCH.md)
- [BIGF 기술제안서](BIGF_기술제안서.pdf)
