# Text-to-SQL 테스트용 PostgreSQL 서버

`dataset/db_schema.json`의 **97개 테이블·1,530개 컬럼**을 PostgreSQL에 생성하고, 기본 **각 50행, 총 4,850행**의 가상 데이터를 넣습니다. 인접한 `text_to_sql_api`와 `text_to_sql_mcp`에서 사용할 수 있는 실제 PostgreSQL 서버입니다.

## Docker로 실행

Docker Engine과 Docker Compose v2가 필요합니다.

Compose는 `postgres:17-bookworm` 기반 이미지를 빌드하여 `text_to_sql_db` 컨테이너를 실행합니다.
PostgreSQL은 컨테이너의 `5432`에서 수신하며 호스트의 `31066`으로 연결합니다. DB는 미리
만든 `text-to-sql-network`에 연결됩니다. 서버에 네트워크가 없으면 최초 한 번 생성합니다.

```bash
docker network create text-to-sql-network
```

최초 실행 전에 `.env.example`을 `.env`로 복사한 뒤 DB를 기동합니다. `docker compose up -d`는
이미지 빌드와 컨테이너 시작을 함께 처리합니다.

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

PostgreSQL이 빈 데이터 디렉터리를 처음 초기화할 때 같은 컨테이너에서 테이블 생성, 샘플 데이터
적재, 검증을 실행합니다. 기존 데이터 디렉터리로 다시 기동하면 그 데이터를 그대로 사용합니다.
`docker compose ps`로 `db`의 `healthy` 상태를 확인하고, 데이터 확인이 필요하면 `seed-db verify`를
실행합니다.

이전 구성의 `seed` 컨테이너가 남아 있다면 업그레이드 시 `docker compose up -d --remove-orphans`
로 정리할 수 있습니다. 기존 바인드 마운트의 데이터는 새 `db` 컨테이너에 연결됩니다.

최초 시드 작업 중 실패하면 미완료 표시가 데이터 디렉터리에 남습니다. 이 상태에서는 PostgreSQL이
재기동하더라도 컨테이너의 상태 확인이 실패합니다. `docker compose logs db`에서 원인을 확인해 수정하고,
필요하면 `docker compose up -d`로 DB를 기동한 뒤 다음 명령을 실행합니다.

```bash
docker compose exec -u postgres db seed-db seed
docker compose exec -u postgres db seed-db verify
```

시드 작업이 성공하면 미완료 표시가 해제됩니다. 같은 `seed` 명령으로 이전 구성에서 샘플 데이터가
적재되지 않은 기존 DB를 초기화할 수도 있습니다.

기본 검증 결과는 다음과 같습니다. 마지막 항목에는 자료형 충돌로 제외된 관계 4개가 표시됩니다.

```json
{
  "status": "verified",
  "tables": 97,
  "columns": 1530,
  "rows_per_table": 50,
  "total_rows": 4850,
  "seed": 42,
  "relationships_checked": 122,
  "excluded_relationships": [
    "PROFIT_CENTER_GROUP.DLC_KEY (DATE) -> SIS_DEPARTMENT.DLC_KEY (VARCHAR2)",
    "FAC_FLOOR.BUILDING_KEY (DATE) -> BUILDINGS.BUILDING_KEY (VARCHAR2)",
    "FCLT_BUILDING_ADDRESS.FCLT_BUILDING_KEY (DATE) -> FCLT_BUILDING.FCLT_BUILDING_KEY (VARCHAR2)",
    "CIS_COURSE_CATALOG.DEPARTMENT_CODE (DATE) -> SIS_DEPARTMENT.DEPARTMENT_CODE (VARCHAR2)"
  ]
}
```

호스트에서의 기본 접속정보는 다음과 같습니다. 비밀번호는 `.env`의 `DB_PASSWORD`를 사용합니다. 예제 파일에는 로컬 샘플용 비밀번호가 들어 있습니다.

| 항목 | 기본값 |
| --- | --- |
| 호스트 | `127.0.0.1` |
| 포트 | `31066` |
| DB명 / 사용자 | `text_to_sql` / `text_to_sql` |
| 스키마 | `public` |
| 테이블·컬럼 이름 | 소문자 |

```bash
psql -h 127.0.0.1 -p 31066 -U text_to_sql -d text_to_sql -W
```

컨테이너 내부의 psql을 사용할 수도 있습니다. 환경변수는 컨테이너 안에서 확장합니다.

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## 데이터 저장 경로

PostgreSQL 데이터는 프로젝트 하위 폴더를 사용하는 바인드 마운트에 저장합니다.

| 구분 | 경로 |
| --- | --- |
| 호스트 서버 | Compose 파일이 있는 `text_to_sql_db` 폴더의 `data/postgres` |
| Compose의 상대 경로 | `./data/postgres` |
| 컨테이너 내부 | `/text_to_sql_db/data/postgres` |
| PostgreSQL의 `PGDATA` | `/text_to_sql_db/data/postgres` |

호스트 상대 경로는 `compose.yaml`이 있는 디렉터리를 기준으로 해석합니다. 예를 들어 프로젝트가
`/srv/text_to_sql_db`에 있다면 호스트 데이터 경로는 `/srv/text_to_sql_db/data/postgres`입니다.
PostgreSQL 프로세스가 해당 데이터 디렉터리를 읽고 쓸 수 있어야 합니다. `data/`는 Git과
Docker 빌드 컨텍스트의 제외 대상입니다.

기존 named volume `text-to-sql-db_postgres_data`의 데이터를 유지하려면 적용 전에 DB를 정상
종료하고 백업·복원 또는 정지 상태의 데이터 복사로 새 호스트 경로에 데이터를 이관해야 합니다.
이름은 별도 Compose 프로젝트명 지정 여부에 따라 달라질 수 있습니다. Compose 설정 변경은
기존 볼륨 데이터를 자동으로 옮기거나 삭제하지 않습니다. 비어 있는 호스트 폴더로 시작하면
`db` 컨테이너에서 PostgreSQL 초기화와 샘플 데이터 적재가 실행됩니다.

## 설정과 데이터 재생성

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `DB_HOST` | `127.0.0.1` | Compose의 호스트 바인딩 주소. Python 직접 실행 시 DB 접속 호스트 |
| `DB_PORT` | `31066` | Python 직접 실행 시 접속 포트. Compose 공개 포트는 `31066`으로 고정 |
| `DB_NAME` | `text_to_sql` | DB 이름 |
| `DB_USER` | `text_to_sql` | DB 사용자 |
| `DB_PASSWORD` | 없음 | Compose에서는 필수. `.env.example` 참고 |
| `DB_SCHEMA` | `public` | 테이블을 생성할 스키마. 설정한 대소문자를 그대로 사용 |
| `SEED_ROWS` | `50` | **모든 테이블에 각각** 삽입할 행 수. 1 이상의 정수 |
| `SEED` | `42` | 데이터 생성 시드 |

Compose의 호스트 공개 포트는 `31066`으로 고정됩니다. 컨테이너 안의 `seed-db`는
`POSTGRES_*` 설정을 사용해 로컬 Unix 소켓으로 접속합니다. Python 직접 실행 시 프로젝트
루트의 `.env`를 읽고, 이미 설정된 프로세스 환경변수가 우선합니다.

`seed-db seed`를 동일한 설정으로 다시 실행하면 스키마·행 수·전체 데이터·주요 관계를 검증한 뒤
`status=skipped`로 종료합니다. 대상 테이블이 없거나 모두 비어 있을 때 샘플 데이터를 적재합니다.
일부 데이터만 남아 있거나 값·행 수·시드가 다르면 오류로 종료하고 기존 데이터를 보존합니다.

설정을 바꾸어 데이터를 다시 만들려면 `.env`의 `SEED_ROWS`, `SEED`를 변경하고
`docker compose up -d --wait`로 컨테이너에 적용한 뒤 다음을 실행합니다. `--reset`은
**원본에 있는 대상 97개 테이블의 기존 행을 모두 삭제하고 재생성**합니다.

```bash
docker compose exec -u postgres db seed-db seed --reset
docker compose exec -u postgres db seed-db verify
```

스키마 생성·데이터 삽입·사후 검증은 하나의 트랜잭션입니다. `--reset`의 삭제도 삽입 실패 시 함께 롤백됩니다. 기존 컬럼 구성이 다르면 `--reset`에서도 자동 변경하지 않습니다. 대상 밖의 테이블을 삭제하거나 `TRUNCATE CASCADE`를 실행하지 않습니다. 같은 스키마에 대한 초기화 작업은 잠금으로 직렬화합니다.

서버를 중지하거나 컨테이너를 다시 생성해도 호스트의 `data/postgres` 데이터는 유지됩니다.

```bash
docker compose down
docker compose up -d
```

`DB_NAME`, `DB_USER`, `DB_PASSWORD`는 빈 데이터 디렉터리에서 PostgreSQL을 처음
초기화할 때 적용됩니다. 기존 데이터 디렉터리의 DB나 사용자·암호는 `.env` 수정만으로
변경되지 않습니다. `docker compose down -v`도 바인드 마운트인 호스트의 `data/postgres`
내용을 삭제하지 않습니다. 새 DB로 다시 초기화하려면 DB를 정상 종료하고 필요한 데이터를
백업한 뒤 호스트 데이터 디렉터리를 별도로 정리해야 합니다. 대상 테이블의 샘플 데이터만
재생성하려면 위의 `seed --reset` 절차를 사용합니다.

## 스키마와 가상 데이터

원본 JSON을 수정하지 않고 DDL을 메타데이터로 파싱하여 PostgreSQL DDL을 별도로 생성합니다. 알 수 없는 자료형이나 중복·잘못된 식별자는 DB 작업 전에 오류로 처리합니다. SQL 식별자는 psycopg `Identifier`, 데이터는 바인딩 매개변수로 전달합니다.

| 원본 자료형 | PostgreSQL 자료형 |
| --- | --- |
| `VARCHAR2` | `text` |
| `NUMBER` | `numeric` |
| `CHAR` | `char(1)` |
| `DATE` | `timestamp(0) without time zone` |

Oracle `DATE`에 포함될 수 있는 시·분·초를 보존하도록 timestamp를 사용합니다. 시간대와 소수 초는 추가하지 않습니다. 원본에는 기본 키·외래 키 제약이 없으므로 이를 임의로 추가하지 않습니다. 테이블 설명은 PostgreSQL 테이블 주석에 저장합니다.

공통 인물·조직·학기·과목·건물·층·호실·교재 값을 재사용하여 주요 조인이 가능합니다. 과목의 학기·학과, 이름과 이메일, 수업 단위 합계, 학기 시작·종료 순서, 교재 가격 등에 일관된 값을 사용합니다. 범주와 참조 키에는 중복을 포함하여 집계를 시험할 수 있습니다. 중간 이름에는 NULL을 포함합니다. 이메일과 URL에는 `example.com`을 사용합니다.

데이터는 시드와 고정 기준 시점 **2026-01-15 12:00:00**으로 생성하며 실행 날짜나 테이블 순서에 따라 달라지지 않습니다. `IS_CURRENT_TERM`은 이 가상 기준의 현재 학기를 뜻합니다. 모든 컬럼에는 선언된 자료형의 값을 생성하고, 별도 의미 규칙이 없는 컬럼에는 이름에 따른 샘플 값을 채웁니다. 실제 기관 데이터나 운영 데이터 분포를 재현하는 용도는 아닙니다.

원본의 자료형 오류는 그대로 유지합니다. 예를 들어 `CIS_COURSE_CATALOG.DEPARTMENT_NAME DATE`에는 날짜를, `FAC_BUILDING.BUILDING_NAME NUMBER`에는 숫자를 넣습니다. `FAC_ROOMS.WAREHOUSE_LOAD_DATE NUMBER`도 숫자로 유지합니다. 위 검증 결과의 4개 관계는 서로 자료형이 달라 조인을 보장하지 않습니다. 이름이 비슷한 모든 컬럼을 관계로 추정하지 않으며, 명시된 키 계열 122개만 참조 일관성을 검증합니다.

대표 쿼리는 [examples/queries.sql](examples/queries.sql)에 있습니다. psql에서 실행하려면 다음을 사용합니다.

```bash
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < examples/queries.sql
```

`DB_SCHEMA`를 바꾼 경우 예제의 `search_path`를 해당 스키마로 변경하세요.

## API·MCP 연결

인접 `text_to_sql_mcp` 프로젝트의 `.env`에는 위 DB 접속정보와 함께 다음 값을 설정합니다. `.env.example`의 `DB_IDENTIFIER_CASE`는 조회 클라이언트용 값이며, 생성기는 항상 소문자 테이블·컬럼을 만듭니다.

```dotenv
DB_HOST=127.0.0.1
DB_PORT=31066
DB_NAME=text_to_sql
DB_USER=text_to_sql
DB_PASSWORD=local_sample_password
DB_SCHEMA=public
DB_IDENTIFIER_CASE=lower
```

MCP도 컨테이너로 실행한다면 DB와 같은 `text-to-sql-network`에 연결하고 MCP의 `.env`에서
`DB_HOST=text_to_sql_db`, `DB_PORT=5432`를 사용합니다. 실제 비밀번호나 스키마를 변경한
경우 MCP 설정에도 반영하세요.

## Python 직접 실행과 테스트

Python 3.10 이상과 접근 가능한 PostgreSQL이 있다면 Docker 없이 초기화·검증 CLI를 실행할 수 있습니다. DB와 사용자는 먼저 만들어져 있어야 하며 테이블·스키마 생성 권한이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
cp .env.example .env
# .env에 접속할 DB 설정 입력
python3 -m seed_db seed
python3 -m seed_db verify
python3 -m pytest -q
```

기본 테스트는 스키마 파싱, 전체 자료형, 재현성, 날짜·가격·단위 관계 및 122개 참조 관계를 검사합니다. 실제 DB 테스트는 `TEST_DATABASE_URL`을 명시한 경우에만 실행합니다. 테스트는 각자 고유한 임시 스키마를 만들고 종료 시 해당 스키마를 삭제하며 `.env`의 DB를 자동으로 사용하지 않습니다.

```bash
TEST_DATABASE_URL='postgresql://test_user:test_password@127.0.0.1:5432/test_db' \
  python3 -m pytest -q
```

통합 테스트는 97개 테이블의 실제 생성·삽입, 주요 조인과 집계, 재실행 중복 방지, 다른 행 수·시드로 재생성, 부분 초기화·스키마 불일치 처리, 삽입 및 재생성 실패 시 롤백, 대상 밖 테이블 보존, CLI 결과를 확인합니다.

구현 시 Python 3.10과 격리된 임시 PostgreSQL 16.2에서 단위 테스트 20개·통합 테스트 8개를 통과했습니다. Compose는 PostgreSQL 17 이미지를 사용합니다. 현재 작업 환경에는 Docker가 없어 이미지 빌드와 Compose를 통한 서버 기동은 검증하지 못했습니다.
