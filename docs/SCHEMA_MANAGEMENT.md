# DB schema 관리와 배포

## Source of truth

`init/01_schema.sql`이 테이블, 인덱스, 뷰, 검색 함수의 유일한 canonical source다.
`fund-db-handoff/schema.sql`은 이 파일을 `\ir`로 불러오는 psql 호환 진입점이며 자체
DDL을 갖지 않는다. 따라서 스키마 변경은 `init/01_schema.sql`에서 한 번만 한다.

앱이 사용하는 `audit`, `core`, `meta`, `raw`, `search` schema 이름은 SQL 계약의
일부이므로 환경별로 바꾸지 않는다. DB role/owner와 pg_dump 주석 같은 배포 메타데이터는
canonical 파일에 저장하지 않고 standalone 산출물을 만들 때만 넣는다.

```bash
python scripts/schema_artifacts.py --owner ncp_app \
  --output build/handoff-schema.sql
```

`--owner`를 생략하면 canonical 내용과 byte-for-byte 같은 standalone 파일이 생성된다.
owner를 주면 구조 생성 뒤 catalog를 조회해 앱 schema의 테이블, partitioned table,
sequence, view, materialized view, function, schema owner를 안전하게 바꾸는 psql 구문이
뒤에 붙는다. 생성된 `build/` 파일은 환경 산출물이며 Git에 커밋하지 않는다.

## 신규 Docker DB

`docker compose up`이 빈 `pgdata` volume을 처음 만들 때 PostgreSQL entrypoint가 다음을
파일명 순서대로 한 번 실행한다.

1. `init/00_init.sql`: `vector`, `pg_trgm` extension
2. `init/01_schema.sql`: canonical schema

초기화 파일은 기존 volume에서는 다시 실행되지 않는다. 개발 DB를 다시 만드는 작업은
데이터를 지우므로 필요한 데이터가 없는지 확인한 뒤 별도로 수행한다.

## 기존 NCP DB

기존 DB에는 전체 초기화 dump를 실행하지 않는다. 다음 순서를 지킨다.

1. NCP backup/snapshot을 만들고 복구 가능 여부를 확인한다.
2. 동일한 PostgreSQL major version의 staging 복제본에서 dry-run과 migration을 실행한다.
3. 적용할 서비스가 쓰는 것과 같은 `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`,
   `PGPASSWORD`를 안전한 실행 환경에 설정한다.
   runner는 host `psql`을 사용하며 `.env`나 `DATABASE_URL`을 자동으로 읽지 않는다.
   외부 포트를 열지 않은 현재 NCP 구성에서는 DB와 같은 서버에서 loopback/private
   주소로 접속한다. 다른 호스트의 DB에 접속할 때는 인증서와 일치하는 DNS 이름,
   `PGSSLMODE=verify-full`, 승인된 `PGSSLROOTCERT`를 사용한다.
4. `python scripts/apply_migrations.py --dry-run`으로 대기 버전을 확인한다.
5. maintenance window에 `python scripts/apply_migrations.py`를 실행한다.
6. `TABLE meta.schema_migration`과 검색 회귀 시나리오를 확인한 뒤 서비스를 재개한다.

runner는 한 `psql` session과 transaction 안에서 advisory lock을 먼저 얻고 적용 이력을
다시 확인한 뒤 아직 실행되지 않은 migration만 적용한다. 따라서 두 배포가 겹쳐도 두
번째 실행은 첫 번째 commit 뒤의 최신 이력을 확인한다. 버전, 설명, SHA-256 checksum,
적용 시각, 적용 role은 `meta.schema_migration`에 남는다. 이미 적용된 파일이 바뀌면
실패한다. rollback이 필요하면 자동 down migration 대신 1단계 backup을 복구한다.

`V20260905_001__sync_active_snapshot_search_functions.sql`은 이슈 #15에서 수정된
`search.find_overseas_strategies`와 `search.find_products`를 `CREATE OR REPLACE`로
적용한다. 두 함수 모두 ACTIVE data version에 연결된 snapshot만 후보에 포함한다.

## 변경 절차

1. `init/01_schema.sql`을 수정한다.
2. 기존 DB에도 필요한 변경이면 새 `VYYYYMMDD_NNN__description.sql`을 추가한다.
   적용된 migration은 절대 수정하지 않는다.
3. 검색 함수 변경은 새 migration에 canonical과 같은 `CREATE OR REPLACE FUNCTION`
   정의를 넣는다.
4. 아래 검사를 로컬에서 실행한다.

```bash
python scripts/schema_artifacts.py --check
python -m unittest tests.test_schema_active_versions tests.test_schema_source_of_truth
```

GitHub의 `Schema drift` workflow도 같은 검사를 수행한다. handoff 진입점에 복사 DDL이
생기거나 owner가 canonical에 들어가거나 canonical 검색 함수와 기존 DB migration이
달라지면 PR 검사가 실패한다.
