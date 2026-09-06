# 네이버클라우드(NCP) 배포 가이드

Docker 이미지는 준비됐으니, 실제 서버 배포는 이 순서대로 진행하면 됩니다.
(이 부분은 NCP 콘솔 접근이 필요해서 직접 실행은 못 하고, 순서만 정리했습니다.)

## 1. 서버(Server) 인스턴스 생성

- NCP 콘솔 → Server → 서버 생성
- 이미지: Ubuntu 22.04 이상
- 사양: 최소 vCPU 2 / Memory 4GB 권장 (HCX 호출 자체는 서버 부하가 크지 않지만 여유 있게)
- Public IP 신청 (평가 서버는 고정 IP 필요)
- 인증키 생성 및 다운로드 (.pem) — 이거 없으면 서버 SSH 접속 자체가 안 되니 꼭 안전하게 보관

## 2. ACG(방화벽) 설정

- Server → ACG → 규칙 추가
- 인바운드: TCP 22 (SSH, 본인 IP만 허용 권장), TCP 8000 (API, 전체 허용 또는 평가서버 IP 대역)
- 아웃바운드: 기본 전체 허용 (HCX API, DB 등 외부 호출 필요)

## 3. 서버 접속 및 Docker 설치

```bash
ssh -i <다운받은키>.pem root@<서버 Public IP>

# Docker 설치 (Ubuntu 기준)
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# docker compose 플러그인 확인
docker compose version
```

## 4. 코드 배포

```bash
git clone https://github.com/Jayams11/miraeasset-bigf.git
cd miraeasset-bigf

# .env 실제 값으로 채우기 (서버에서 직접 작성, git에는 올리지 않음)
cp .env.example .env
nano .env
```

## 5. 컨테이너 실행

DB 경로를 먼저 구분합니다.

- `pgdata`가 없는 신규 DB: `init/00_init.sql` 뒤에 canonical
  `init/01_schema.sql`이 자동 실행됩니다.
- 기존 NCP DB 또는 기존 volume: 초기화 SQL은 다시 실행되지 않습니다. 먼저 백업한 뒤
  아래 migration 명령으로만 갱신합니다.

`apply_migrations.py`는 서버에 설치된 `psql`을 사용하며 `.env` 또는
`DATABASE_URL`을 자동으로 읽지 않습니다. 현재처럼 PostgreSQL 포트를 외부에 열지 않는
NCP 구성에서는 **DB와 같은 NCP 서버의 터미널**에서 실행하고, 먼저 다음 연결 정보를
해당 터미널 세션에 설정합니다. 실제 값은 비밀 저장소 또는 서버 관리자에게서 받아야 하며
Git에는 기록하지 않습니다.

```bash
psql --version
export PGHOST=127.0.0.1
export PGPORT=5432
export PGDATABASE="운영_DB_이름"
export PGUSER="migration_권한_DB_사용자"
read -s PGPASSWORD
export PGPASSWORD

psql -X -v ON_ERROR_STOP=1 -c 'SELECT current_database(), current_user;'
```

위 확인이 성공한 상태에서만 dry-run과 실제 적용을 순서대로 실행합니다. DB가 다른 내부
호스트에 있다면 IP가 아닌 **서버 인증서와 일치하는 내부 DNS 이름**을 `PGHOST`에 쓰고,
아래처럼 서버 인증까지 검증하는 TLS 연결을 사용합니다. `PGSSLROOTCERT`에는 NCP에서
승인한 CA 인증서 경로 또는 팀이 승인한 system trust store 경로를 지정합니다.

```bash
export PGHOST="db.internal.example"
export PGSSLMODE=verify-full
export PGSSLROOTCERT="/secure/path/to/approved-ca.pem"
psql -X -v ON_ERROR_STOP=1 -c 'SELECT current_database(), current_user;'
```

인증서 검증을 끄거나 `sslmode=require`만 사용하는 방식은 원격 migration에 사용하지
않습니다. Docker Compose의 `db` 서비스처럼 호스트 포트가 전혀 공개되지 않은 DB에는
이 host runner가 직접 연결할 수 없습니다. 신규 Compose DB는 `init/` 경로를 사용하고,
기존 volume을 갱신해야 한다면 외부 공개 없이 host loopback 또는 같은 Docker network
안에서 `psql`이 도달하도록 C가 관리 접속 경로를 먼저 마련해야 합니다.

```bash
python scripts/apply_migrations.py --dry-run
python scripts/apply_migrations.py
unset PGPASSWORD
```

적용 이력은 `meta.schema_migration`에서 확인합니다. 적용된 migration 파일의 checksum이
달라지면 runner가 중단되므로 기존 파일을 고치지 말고 새 버전을 추가해야 합니다.

```bash
docker compose up -d --build
docker compose ps        # 상태 확인
docker compose logs -f api   # 로그 확인
```

## 6. 자동 재시작 확인

`docker-compose.yml`에 `restart: unless-stopped`가 이미 들어있어서
서버가 재부팅되거나 컨테이너가 죽어도 자동으로 다시 뜹니다. 확인 방법:

```bash
# 컨테이너 강제 종료 후 살아나는지 확인
docker kill $(docker compose ps -q api)
sleep 5
docker compose ps   # 다시 Up 상태여야 정상
```

## 7. 외부에서 실제 호출 테스트

**반드시 노트북이 아니라 휴대폰 핫스팟 등 다른 네트워크에서** 확인:

```bash
curl "http://<서버 Public IP>:8000/health"
curl "http://<서버 Public IP>:8000/answer?question_id=Q-001&question=테스트"
```

`/answer` 검색 전에는 운영 DB의 데이터 버전과 함수가 준비됐는지 확인합니다.

```sql
SELECT version_name, status FROM meta.data_version;
SELECT count(*) FROM search.filter_products(p_product_type => 'DOMESTIC_ETP');
```

실제 사용할 버전이 `ACTIVE`여야 합니다. 검색 함수가 정상 실행되는데 결과가 0건이면 데이터
버전과 snapshot 연결을 먼저 확인합니다. DB 접속 또는 SQL 실행 오류라면 `/answer`의
`think_trace`가 `structured_search:failed`와 `RETRIEVAL_ERROR`를 표시해야 하며, 정상 0건과
구분됩니다.

예시 검증:

```bash
curl -G "http://<서버 Public IP>:8000/answer" \
  --data-urlencode "question_id=gateway-smoke-001" \
  --data-urlencode "question=순자산 1000억원 이상 국내 ETF를 순자산 큰 순으로 1개 추천해줘"
```

정상 데이터가 있으면 `think_trace`에 `structured_search:success`가, `retrieved_context`에는
상품 ID와 원본 출처가 포함되어야 합니다.

## 8. (선택) HTTPS 적용

- 도메인이 있다면 Nginx + Let's Encrypt(certbot)로 리버스 프록시 구성 권장
- 시간 부족하면 v1은 HTTP로 제출하고 README에 명시

## 오류 로그 확인

애플리케이션 오류는 `operation_failed`와 함께 `stage`, `error_type`을 기록합니다.
API 경계에서는 `request_id`와 `question_id`, 파이프라인에서는 `question_id`,
검색 단계에서는 `step_id`도 남깁니다. 예를 들어
`stage=db.retrieve error_type=OperationalError`는 DB 조회 경계의 연결 오류이고,
`stage=retrieve step_id=structured_search`는 실패한 검색 단계를 가리킵니다.

DB 비밀번호·DSN·토큰이 포함될 수 있는 예외 메시지, 예외 객체, traceback은
애플리케이션 로그에 전달하지 않습니다. 검색 결과의 실패 메시지도 오류 종류만
보존합니다. 오류 로깅을 추가할 때는 `b_agent.safe_logging.log_failure()`를 사용하고,
`str(exc)`나 `logger.exception()`으로 원문을 다시 기록하지 않습니다.

## 체크리스트

- [ ] Public IP로 서버 생성됨
- [ ] ACG에 8000 포트 열림
- [ ] Docker 설치 및 정상 동작
- [ ] `.env`가 서버에만 있고 git에는 없음
- [ ] `docker compose up -d --build` 로 정상 기동
- [ ] 컨테이너 강제 종료 후 자동 재시작 확인
- [ ] 외부 네트워크(휴대폰 등)에서 `/health`, `/answer` 정상 응답 확인
- [ ] `meta.data_version`의 운영 버전이 `ACTIVE`인지 확인
- [ ] `/answer`에 `structured_search:success`와 실제 `retrieved_context`가 포함되는지 확인
- [ ] 서버 재부팅 후에도 자동으로 컨테이너 다시 뜨는지 확인 (`systemctl reboot` 후 재확인)
- [ ] 기존 DB라면 backup 후 migration dry-run/적용 및 `meta.schema_migration` 확인
