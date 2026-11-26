# PostgreSQL 컬럼 조회 MCP 서버

`dataset/db_schema.json`에 정의된 **97개 테이블의 1,530개 컬럼**을 각각 MCP 도구로 제공합니다. LLM 서버의 MCP 클라이언트는 **Streamable HTTP**로 연결하며, 각 도구는 실제 PostgreSQL에서 해당 컬럼 데이터를 조회합니다.

## 설치와 실행

Python 3.10 이상이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

`.env`에 실제 DB 접속정보를 입력합니다.

```dotenv
DB_HOST=localhost
DB_PORT=31066
DB_NAME=your_database
DB_USER=your_readonly_user
DB_PASSWORD=your_password
DB_SCHEMA=public
DB_IDENTIFIER_CASE=lower
DB_SSLMODE=prefer
DB_CONNECT_TIMEOUT=10
DB_STATEMENT_TIMEOUT_MS=30000
MCP_HOST=127.0.0.1
MCP_PORT=31068
```

```bash
python3 server.py
```

기본 MCP 엔드포인트는 **`http://127.0.0.1:31068/mcp`**입니다. 서버는 별도 프로세스로 실행하고 LLM 서버에서 URL로 접속합니다. DB 연결은 도구 호출 시 수행하므로 접속정보 없이도 MCP 초기화와 도구 목록 조회가 가능합니다. 조회에는 `DB_NAME`, `DB_USER`가 필요하며, 암호 없는 인증을 쓰는 DB에서는 `DB_PASSWORD`를 비워둘 수 있습니다.

프로젝트 루트의 `.env`를 읽으며, **이미 설정된 프로세스 환경변수가 우선**합니다. 작업 디렉터리가 달라도 메타데이터와 `.env`는 프로젝트 루트를 기준으로 읽습니다. `.env`는 Git 제외 대상으로 설정했습니다.

## MCP 클라이언트 등록 예시

서버를 먼저 실행한 뒤, Streamable HTTP를 지원하는 클라이언트에 URL을 등록합니다. 클라이언트에 따라 설정 형식은 다를 수 있습니다.

```json
{
  "mcpServers": {
    "postgres-columns": {
      "type": "http",
      "url": "http://127.0.0.1:31068/mcp"
    }
  }
}
```

Python에서는 `mcp.client.streamable_http.streamable_http_client()`와 `ClientSession`을 사용합니다. 초기화·도구 목록·호출의 전체 예제는 [run_guide.md](run_guide.md)의 6절에 있습니다. SDK가 MCP 세션과 프로토콜 헤더를 관리합니다.

HTTP 설정은 `MCP_HOST`, `MCP_PORT`, `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`로 관리합니다. 기본 허용 Host는 localhost·loopback과 Docker 이름 `mcp-server`이며, Origin은 HTTP localhost·loopback입니다. 각 포트 변형을 허용하고, Origin이 없는 서버 간 요청도 받습니다. 다른 내부 주소로 접속하면 `.env.example`의 허용 목록에 해당 Host를 추가합니다. 바인딩 주소를 변경해도 허용 목록은 자동 변경되지 않습니다.

이 서버는 사설 네트워크 사용을 전제로 하며 별도 토큰 인증은 추가하지 않았습니다.

## Docker 실행

컨테이너를 시작하면 Bash 셸이 열립니다. 아래 예시는 DB와 같은 `text-to-sql-network`에
연결합니다. `.env.example`을 `.env`로 복사하면 DB 접속 주소로 `text_to_sql_db:5432`를 사용합니다.

```bash
docker build -t text-to-sql-mcp .
docker run --rm -it --name text_to_sql_mcp --network text-to-sql-network \
  --env-file .env text-to-sql-mcp
```

컨테이너 셸에서 MCP 서버를 시작합니다.

```bash
python3 server.py
```

같은 Docker 네트워크의 클라이언트는 `http://text_to_sql_mcp:31068/mcp`로 접속합니다.
호스트에서 직접 접속하려면 `-p 127.0.0.1:31067:31068`을 추가하고
`http://127.0.0.1:31067/mcp`를 사용합니다. 자세한 실행·DB 연결 방법은 [run_guide.md](run_guide.md)의 10절을 참고하세요.

## 도구 호출

도구명은 `get_{소문자 테이블명}_{소문자 컬럼명}`입니다. 예를 들어 `FCLT_ORG_DLC_KEY.DLC_KEY`를 조회하는 도구는 `get_fclt_org_dlc_key_dlc_key`입니다.

모든 도구의 인자는 같습니다.

| 인자 | 기본값 | 설명 |
| --- | --- | --- |
| `filters` | `null` | 같은 테이블의 컬럼명과 일치시킬 값. 여러 조건은 AND로 결합 |
| `limit` | `100` | 반환할 값 개수, 1~1,000의 정수 |
| `offset` | `0` | 건너뛸 값 개수, 0 이상의 정수 |
| `distinct` | `false` | `true`이면 중복값 제거 |

조회할 테이블·컬럼은 도구에 고정되어 있습니다. `filters`의 키는 메타데이터에 있는 **원본 대문자 컬럼명**을 사용하며, 사용 가능한 키는 각 도구 설명에 나옵니다. 필터 값은 문자열·숫자·불리언·`null`을 받으며 DB 컬럼 자료형에 맞는 값을 전달합니다. `null`은 SQL `IS NULL`로 조회합니다. 숫자나 날짜 컬럼의 필터에는 정밀한 숫자 문자열 또는 ISO 날짜 문자열도 사용할 수 있습니다.

```json
{
  "name": "get_fclt_org_dlc_key_dlc_key",
  "arguments": {
    "filters": {"FCLT_ORGANIZATION_KEY": "ORG001"},
    "limit": 2,
    "offset": 0,
    "distinct": false
  }
}
```

반환 예시 (`structuredContent`):

```json
{
  "table": "FCLT_ORG_DLC_KEY",
  "column": "DLC_KEY",
  "values": ["DLC001", "DLC001"],
  "returned_count": 2,
  "limit": 2,
  "offset": 0,
  "distinct": false,
  "has_more": true
}
```

값은 대상 컬럼의 DB 정렬 규칙에 따라 오름차순으로 반환하고 NULL은 마지막에 둡니다. 기본적으로 중복과 NULL을 유지하며, `distinct=true`일 때 NULL은 최대 하나를 포함합니다. 다음 페이지는 `offset + returned_count`로 조회합니다. 페이지 사이에 DB 데이터가 변경되면 조회 결과도 달라질 수 있습니다.

날짜·시간은 ISO 8601 문자열, PostgreSQL `numeric`/`decimal`의 `Decimal` 값은 정밀도를 보존하는 문자열, SQL NULL은 JSON `null`로 반환합니다. 다른 기본 숫자와 문자열은 원래 JSON 자료형을 유지합니다. 무한대·NaN 부동소수점 값은 문자열로 반환합니다.

## 메타데이터와 식별자

- `dataset/db_schema.json`: 테이블명, DDL 형태의 컬럼 정의, 원본 테이블 설명입니다.
- `dataset/column_descriptions.json`: `{테이블명: {컬럼명: 한국어 설명}}` 형태의 컬럼별 설명입니다. 원본 description의 테이블 맥락과 컬럼명을 근거로 작성했으며 불명확한 약어는 그대로 유지합니다.
- 실행 시 두 파일의 테이블·컬럼 목록과 설명 누락, 중복, 도구명 충돌을 검증합니다. 메타데이터를 변경하면 컬럼 설명도 함께 수정하고 서버를 재시작합니다.

원본 DDL의 `VARCHAR2`, `NUMBER`, `CHAR`, `DATE`는 메타데이터 자료형입니다. 서버는 DDL을 실행하거나 테이블을 생성하지 않습니다. 정의된 테이블과 컬럼은 실제 PostgreSQL에 이미 존재해야 합니다.

`DB_IDENTIFIER_CASE=lower`는 원본의 대문자 테이블·컬럼명을 PostgreSQL의 일반적인 소문자 식별자로 바꿔 조회합니다. 실제 DB가 `"FCLT_ORG_DLC_KEY"`, `"DLC_KEY"`처럼 **인용된 대문자 이름**으로 생성되었다면 `preserve`로 설정합니다. `DB_SCHEMA`는 설정한 철자 그대로 사용합니다. 서로 다른 대소문자가 섞인 식별자는 현재 메타데이터 이름에 맞춰야 합니다.

등록된 컬럼만 조회·필터할 수 있으며, 식별자는 psycopg의 `Identifier`, 필터 값은 바인딩 매개변수로 처리합니다. 각 호출은 별도의 비동기 연결과 읽기 전용 트랜잭션을 사용합니다. 쿼리에는 기본 30초 제한을 적용하고 호출 종료 시 연결을 닫습니다. 연결 오류, 권한 오류, 메타데이터 불일치, 잘못된 필터, 시간 초과는 MCP 도구 오류(`isError=true`)로 반환합니다. 원시 DB 오류나 비밀번호를 로그·응답에 포함하지 않습니다.

## 테스트

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

스키마 파싱·설명 완전성, 1,530개 도구 등록, 입력 검증, 안전한 SQL 생성, 읽기 전용 연결과 오류 처리, JSON 직렬화 및 실제 HTTP 초기화·도구 목록·호출을 검증합니다. 독립적인 클라이언트 세션과 Host·Origin 허용·차단도 확인합니다. 기본 테스트의 DB 응답은 대체 객체를 사용합니다.

실제 PostgreSQL 테스트는 `TEST_DATABASE_URL`을 명시적으로 설정했을 때만 실행합니다. **테스트 전용 DB**를 지정하세요. 테스트는 고유한 임시 스키마에 테이블과 데이터를 생성한 뒤 해당 스키마를 삭제합니다. 기본 `.env` 접속정보를 테스트 DB로 자동 사용하지 않습니다.

```bash
TEST_DATABASE_URL='postgresql://test_user:test_password@localhost:5432/test_db' \
  python3 -m pytest -q -m integration
```

통합 테스트는 중복·NULL·필터·페이지 조회, 소문자와 인용된 대문자 식별자, 실제 숫자·날짜·시각 자료형 및 읽기 전용 트랜잭션을 검증합니다. `TEST_DATABASE_URL`이 없으면 건너뜁니다.
