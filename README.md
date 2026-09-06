# miraeasset-bigf
미래에셋증권 제10회 AI페스티벌 팀BIGF 레포지토리

## 평가용 API Endpoint

```
GET http://49.50.142.6:8000/answer?question_id={질의 ID}&question={평가 질의}
```

- 인증 헤더 불필요.
- 응답은 `application/json`이며 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer` 5개 필드를 모두 문자열로 반환합니다 (계약: [`contracts/external_api.py`](contracts/external_api.py)).
- 서버 상태 확인용 `GET /health`도 함께 제공합니다.

## 데이터베이스 스키마

DB 구조의 유일한 canonical source는 [`init/01_schema.sql`](init/01_schema.sql)입니다.
새 Docker DB는 이 파일로 초기화하고, 이미 운영 중인 NCP DB는
[`migrations/`](migrations/)의 버전형 migration만 순서대로 적용합니다.
`fund-db-handoff/schema.sql`은 DDL 복사본이 아니라 canonical 파일을 직접 실행하는
호환용 psql 진입점입니다. 변경·배포 절차는
[`docs/SCHEMA_MANAGEMENT.md`](docs/SCHEMA_MANAGEMENT.md)에 정리되어 있습니다.

기업 관계 데이터의 방향·출처·ACTIVE 버전 및 `relation_search` 계약은
[`docs/RELATION_SEARCH.md`](docs/RELATION_SEARCH.md)에 정리되어 있습니다.
