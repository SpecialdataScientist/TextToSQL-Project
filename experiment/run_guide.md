# PostgreSQL 컬럼 조회 MCP 서버 사용 가이드

이 문서는 서버의 설치·실행 방법, MCP 클라이언트 연결 방법, 도구 호출 및 응답 형식을 설명합니다. 명령은 Linux/bash에서 프로젝트 루트를 기준으로 실행합니다.

## 1. 서버가 하는 일

`dataset/db_schema.json`에 정의된 **97개 테이블의 1,530개 컬럼**을 각각 MCP 도구로 제공합니다. 각 도구를 호출하면 PostgreSQL에서 해당 컬럼의 값을 조회합니다.

| 구성 | 역할 |
| --- | --- |
| `server.py` | Streamable HTTP MCP 서버 실행 진입점 |
| `column_mcp/server.py` | 컬럼별 도구와 설명 등록, 입력 검증 |
| `column_mcp/database.py` | 조건에 따른 PostgreSQL 조회와 결과 직렬화 |
| `column_mcp/config.py` | `.env`와 프로세스 환경변수 로딩 |
| `dataset/db_schema.json` | 테이블명, 컬럼 정의, 원본 테이블 설명 |
| `dataset/column_descriptions.json` | 테이블·컬럼별 한국어 설명 |

도구명은 `get_{소문자 테이블명}_{소문자 컬럼명}`입니다.

예를 들어 `FCLT_ORG_DLC_KEY` 테이블의 `DLC_KEY` 컬럼에 해당하는 도구는 `get_fclt_org_dlc_key_dlc_key`입니다. 도구 설명에는 컬럼 의미, 원본 메타데이터 자료형, 조회 방법과 필터로 사용할 수 있는 컬럼 목록이 포함됩니다.

호출 흐름은 다음과 같습니다.

```text
LLM 서버의 MCP 클라이언트 → HTTP /mcp → 도구 입력 검증 → PostgreSQL SELECT → 구조화된 JSON 응답
```

서버는 컬럼 하나씩 조회하며 같은 테이블의 다른 컬럼을 조회 조건으로 사용할 수 있습니다. 현재 지원하는 조건은 일치 비교와 NULL 조회입니다. 조인, 범위·부분 일치 조건, 사용자 지정 SQL 실행 기능은 제공하지 않습니다.

## 2. 설치

Python 3.10 이상과 접속 가능한 PostgreSQL DB가 필요합니다. 조회 대상 테이블과 컬럼은 DB에 이미 존재해야 합니다. 원본 JSON의 `VARCHAR2`, `NUMBER`, `CHAR`, `DATE`는 메타데이터이며, 서버가 해당 DDL을 실행하거나 테이블을 생성하지는 않습니다.

프로젝트 루트에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

의존성에는 공식 MCP Python SDK v1 계열(`mcp>=1.25,<2`), psycopg 3, Pydantic, python-dotenv가 포함됩니다.

## 3. DB와 HTTP 설정

`.env`가 없다면 예시 파일을 복사합니다. 아래 명령은 기존 `.env`를 덮어쓰지 않습니다.

```bash
test -e .env || cp .env.example .env
```

`.env`를 열어 예시값을 실제 접속정보로 변경합니다.

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
MCP_ALLOWED_HOSTS=localhost,localhost:*,127.0.0.1,127.0.0.1:*,[::1],[::1]:*,mcp-server,mcp-server:*
MCP_ALLOWED_ORIGINS=http://localhost,http://localhost:*,http://127.0.0.1,http://127.0.0.1:*,http://[::1],http://[::1]:*
```

| 환경변수 | 미설정 시 기본값 | 설명 |
| --- | --- | --- |
| `DB_HOST` | `localhost` | PostgreSQL 호스트 |
| `DB_PORT` | `31066` | 포트, 1~65,535 |
| `DB_NAME` | 빈 문자열 | 조회 시 필수인 DB 이름 |
| `DB_USER` | 빈 문자열 | 조회 시 필수인 DB 사용자. 대상 스키마와 테이블 조회 권한 필요 |
| `DB_PASSWORD` | 빈 문자열 | DB 비밀번호. 암호 없는 인증을 사용하는 환경에서는 생략 가능 |
| `DB_SCHEMA` | `public` | 조회 대상 PostgreSQL 스키마. 설정한 철자 그대로 사용 |
| `DB_IDENTIFIER_CASE` | `lower` | 테이블·컬럼명 처리 방식: `lower` 또는 `preserve` |
| `DB_SSLMODE` | `prefer` | `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full` 중 하나 |
| `DB_CONNECT_TIMEOUT` | `10` | 연결 제한 시간(초), 양의 정수 |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` | SQL 실행 제한 시간(밀리초), 양의 정수 |
| `MCP_HOST` | `127.0.0.1` | HTTP 서버 바인딩 주소. Docker에서는 `0.0.0.0` 사용 |
| `MCP_PORT` | `31068` | HTTP 포트, 1~65,535. 엔드포인트 경로는 `/mcp`로 고정 |
| `MCP_ALLOWED_HOSTS` | localhost·loopback·`mcp-server` | 허용할 HTTP Host의 쉼표 구분 목록. 기본 목록은 위 예시와 같음 |
| `MCP_ALLOWED_ORIGINS` | HTTP localhost·loopback | Origin 헤더가 있는 요청의 허용 출처. 기본 목록은 위 예시와 같음 |

서버는 실행 작업 디렉터리와 관계없이 **프로젝트 루트의 `.env`**를 읽습니다. 동일한 키가 이미 프로세스 환경변수에 있으면 그 값을 우선합니다. `.env` 변경 후에는 HTTP 서버 프로세스를 재시작합니다.

`MCP_HOST`에는 `http://`나 포트를 포함하지 않습니다. 클라이언트는 실제 접속 가능한 서버 주소를 URL에 사용합니다. `0.0.0.0`은 서버 바인딩 주소입니다.

허용 목록을 지정하면 기본 목록을 대체합니다. Host에는 `mcp-server`, `mcp-server:31068`, `mcp-server:*`처럼 호스트명·IP와 선택적 포트를 사용합니다. `:*`는 모든 포트에 대한 변형이며 호스트명 전체를 허용하는 `*`는 지원하지 않습니다. Origin은 `http://localhost:3000`, `https://llm.internal:*`처럼 스킴·호스트·선택적 포트만 포함하고 경로나 쿼리는 포함하지 않습니다.

다른 내부 IP나 도메인으로 접속할 때는 해당 주소를 `MCP_ALLOWED_HOSTS`에 추가합니다. 예를 들어 `http://192.168.0.10:31068/mcp`로 접속한다면 위 목록 뒤에 `,192.168.0.10:31068`을 추가합니다. `MCP_HOST`를 바꿔도 허용 목록은 자동 변경되지 않습니다. Origin 헤더가 없는 서버 간 요청은 허용합니다.

현재 서버는 사설 네트워크 사용을 기준으로 구성하며 별도 토큰 인증은 추가하지 않았습니다.

### 테이블·컬럼명 대소문자

- 실제 DB 이름이 `fclt_org_dlc_key`, `dlc_key`처럼 소문자이면 `DB_IDENTIFIER_CASE=lower`를 사용합니다. 원본 JSON의 대문자 테이블·컬럼명을 소문자로 변환해 조회합니다.
- 실제 DB 이름이 `"FCLT_ORG_DLC_KEY"`, `"DLC_KEY"`처럼 인용된 대문자 이름이면 `DB_IDENTIFIER_CASE=preserve`를 사용합니다.

이 설정과 관계없이 **도구명은 소문자**, **필터의 컬럼 키는 원본 JSON의 대문자 이름**을 사용합니다. 현재 원본 메타데이터와 다른 철자나 대소문자가 섞인 DB 이름은 자동으로 매핑하지 않습니다.

## 4. 서버 실행과 클라이언트 연결

### 터미널에서 실행

가상환경을 활성화한 뒤 실행합니다.

```bash
python3 server.py
```

기본 접속 URL은 **`http://127.0.0.1:31068/mcp`**입니다. 서버는 HTTP 요청을 기다리며 터미널에서 `Ctrl+C`로 종료합니다. 별도 터미널이나 LLM 서버에서 MCP 클라이언트를 실행해 연결합니다.

연결 방식은 **Streamable HTTP**이며 MCP 프로토콜을 사용합니다. 아래 JSON 호출 예시는 `tools/call`의 인자로, SDK의 `call_tool()`에 전달합니다. 초기화·세션·프로토콜 헤더는 SDK가 관리합니다. 단순 브라우저 GET 요청은 MCP 초기화나 도구 호출을 대신하지 않습니다.

### MCP 클라이언트에서 연결

HTTP 서버를 먼저 실행한 뒤 Streamable HTTP와 `mcpServers` 형식을 지원하는 클라이언트에 URL을 등록합니다. 클라이언트마다 설정 파일 위치나 형식은 다를 수 있습니다. LLM 서버가 다른 머신에 있으면 `127.0.0.1`을 실제 MCP 서버 주소로 바꾸고 해당 Host를 허용 목록에 등록합니다.

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

LLM 서버의 클라이언트는 이미 실행 중인 MCP 서버에 접속합니다. DB 연결은 도구 호출 시 수행하므로 접속정보가 없어도 초기화와 도구 목록 조회는 가능하고, 실제 데이터 조회에는 유효한 DB 설정이 필요합니다.

## 5. 도구 호출 인자

모든 컬럼 도구는 같은 인자를 사용하며, 인자를 모두 생략하면 기본 설정으로 조회합니다.

| 인자 | 자료형 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `filters` | 객체 또는 `null` | `null` | 같은 테이블의 컬럼과 일치시킬 값. 여러 조건은 AND로 결합 |
| `limit` | 정수 | `100` | 반환할 값 개수, 1~1,000 |
| `offset` | 정수 | `0` | 건너뛸 값 개수, 0 이상 |
| `distinct` | 불리언 | `false` | `true`이면 대상 컬럼 값의 중복 제거 |

`filters`의 값은 문자열·숫자·불리언·`null`을 받을 수 있으며 실제 DB 컬럼 자료형에 맞게 전달합니다. `null` 조건은 SQL `IS NULL`로 처리합니다. 날짜는 ISO 형식 문자열, 정밀한 숫자는 숫자 문자열로 지정할 수 있습니다. 필터에 NaN이나 무한대를 사용할 수는 없습니다.

`limit`과 `offset`은 `"10"` 같은 문자열 대신 JSON 정수로, `distinct`는 `"true"` 같은 문자열 대신 JSON 불리언으로 전달합니다. 필터에는 해당 테이블에 등록된 컬럼만 사용할 수 있습니다.

### 기본 조회

```json
{
  "name": "get_fclt_org_dlc_key_dlc_key",
  "arguments": {}
}
```

`DLC_KEY` 값을 기본 100개까지 조회합니다. 값은 오름차순, NULL은 마지막에 반환하며 중복과 NULL을 유지합니다.

### 일치 조건 조회

```json
{
  "name": "get_fclt_org_dlc_key_dlc_key",
  "arguments": {
    "filters": {"FCLT_ORGANIZATION_KEY": "ORG001"},
    "limit": 20
  }
}
```

조직 키가 `ORG001`인 행에서 `DLC_KEY` 값을 조회합니다. `ORG001`은 예시값이며 실제 DB의 값으로 변경합니다. 여러 조건을 지정하려면 같은 `filters` 객체에 컬럼을 추가합니다.

```json
{
  "name": "get_fclt_org_dlc_key_dlc_key",
  "arguments": {
    "filters": {"FCLT_ORGANIZATION_KEY": "ORG001", "DLC_KEY": "DLC001"}
  }
}
```

### NULL 조회

```json
{
  "name": "get_fclt_org_dlc_key_fclt_organization_key",
  "arguments": {
    "filters": {"DLC_KEY": null},
    "limit": 20
  }
}
```

`DLC_KEY`가 NULL인 행에서 조직 키를 조회합니다.

### 중복 제거와 페이지 조회

```json
{
  "name": "get_fclt_org_dlc_key_dlc_key",
  "arguments": {
    "distinct": true,
    "limit": 20,
    "offset": 20
  }
}
```

중복 제거한 `DLC_KEY` 값 중 앞의 20개를 건너뛰고 다음 20개까지 조회합니다. `distinct=true`일 때 NULL은 존재하면 최대 하나 포함합니다. 첫 페이지는 `offset=0`입니다.

## 6. Python에서 초기화·목록 조회·도구 호출

먼저 별도 터미널에서 `python3 server.py`로 서버를 실행합니다. 아래 명령 전체를 **가상환경이 활성화된 프로젝트 루트**에서 실행합니다. `requirements.txt`의 MCP SDK를 사용하며, 클라이언트는 HTTP URL에 연결합니다. 코드가 서버 프로세스를 시작하거나 종료하지 않습니다.

```bash
python3 - <<'PY'
import asyncio
import json
import os
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.environ.get("MCP_URL", "http://127.0.0.1:31068/mcp")
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            print("서버:", initialized.serverInfo.name)

            listed = await session.list_tools()
            print("도구 수:", len(listed.tools))

            tool_name = "get_fclt_org_dlc_key_dlc_key"
            tool = next(item for item in listed.tools if item.name == tool_name)
            print("도구 설명:", tool.description)
            print("입력 스키마:", json.dumps(tool.inputSchema, ensure_ascii=False, indent=2))

            result = await session.call_tool(
                name=tool_name,
                arguments={"limit": 5, "offset": 0, "distinct": False},
            )
            if result.isError:
                for block in result.content:
                    if block.type == "text":
                        print("도구 오류:", block.text)
                return

            print("조회 결과:")
            print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))


asyncio.run(main())
PY
```

성공 시 서버 이름, 도구 수, 선택한 도구의 설명·입력 스키마와 조회 결과가 출력됩니다. 접속정보가 없으면 목록 조회까지 성공하고 데이터 조회 단계에서 도구 오류가 출력됩니다. 다른 서버에 연결하려면 클라이언트 프로세스의 `MCP_URL` 환경변수를 해당 URL로 설정합니다. `MCP_URL`은 위 클라이언트 예제에서 읽는 값이며 서버 설정값은 아닙니다.

필터를 적용하려면 `arguments`에 `"filters": {"FCLT_ORGANIZATION_KEY": "ORG001"}`을 추가합니다. Python 코드에서 JSON의 `true`, `false`, `null`에 해당하는 값은 `True`, `False`, `None`입니다.

## 7. 반환값 해석

성공 응답의 데이터는 Python SDK에서 `result.structuredContent`로 확인합니다. 다음은 형식 설명을 위한 예시입니다. 실제 값은 DB 내용에 따라 달라집니다.

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

| 필드 | 의미 |
| --- | --- |
| `table` | 원본 메타데이터의 테이블명 |
| `column` | 조회한 컬럼명 |
| `values` | 조회한 컬럼 값 배열 |
| `returned_count` | 이번 응답의 값 개수. 전체 행 수가 아님 |
| `limit` / `offset` | 이번 호출에 적용한 페이지 설정 |
| `distinct` | 중복 제거 적용 여부 |
| `has_more` | 같은 조건으로 조회할 값이 뒤에 더 있는지 여부 |

조회 결과가 없으면 `values=[]`, `returned_count=0`, `has_more=false`로 반환합니다. `has_more=true`이면 필터와 `distinct`를 유지한 채 `offset + returned_count`를 다음 호출의 offset으로 사용합니다. 값은 DB의 정렬 규칙에 따라 오름차순이며, 페이지 사이에 DB 내용이 변경되면 결과도 달라질 수 있습니다.

| DB/Python 값 | 반환 형식 |
| --- | --- |
| 문자열·정수·유한한 실수·불리언 | 대응하는 JSON 자료형 |
| PostgreSQL `numeric`/`decimal`이 반환한 `Decimal` | 정밀도를 보존하는 문자열 |
| 날짜·시간 | ISO 8601 문자열 |
| SQL NULL | JSON `null` |
| 무한대·NaN 부동소수점 값 | 문자열 |

오류가 발생하면 `result.isError`가 `true`이고 오류 설명은 `result.content`의 텍스트에 들어 있습니다. 이때 성공 응답의 `structuredContent`를 가정해 접근하지 않고 오류를 먼저 확인합니다.

## 8. 문제 해결

| 증상 또는 오류 | 확인할 내용 |
| --- | --- |
| HTTP 서버에 연결할 수 없음 | 서버 실행 여부, 접속 URL의 `/mcp`, `MCP_HOST`·`MCP_PORT`, Docker 포트·네트워크 확인 |
| HTTP 421 / Invalid Host | 요청 URL의 호스트·포트를 `MCP_ALLOWED_HOSTS`에 추가하고 재시작 |
| HTTP 403 / Invalid Origin | 전달한 Origin이 `MCP_ALLOWED_ORIGINS`에 등록되어 있는지 확인 |
| HTTP 400 / 세션·프로토콜 오류 | MCP SDK로 초기화한 세션에서 호출하는지 확인. 일반 GET만으로 연결을 확인하지 않음 |
| `ModuleNotFoundError` | 서버·클라이언트 가상환경의 `requirements.txt` 설치 여부 확인 |
| `DB_NAME`, `DB_USER` 설정 오류 | 프로젝트 루트 `.env`의 필수 접속정보와 프로세스 환경변수 확인 |
| DB 연결 실패·끊김 | 호스트·포트·DB 이름·사용자·비밀번호, 네트워크 접근과 SSL 설정 확인 |
| 테이블·컬럼 불일치 | 실제 DB 객체명과 `DB_SCHEMA`, `DB_IDENTIFIER_CASE`, 원본 메타데이터 확인 |
| 조회 권한 없음 | DB 사용자에게 해당 스키마와 테이블 조회 권한이 있는지 확인 |
| 등록된 컬럼명만 사용 가능 | 필터 키가 같은 테이블의 원본 대문자 컬럼명인지 도구 설명에서 확인 |
| 입력 검증 실패 | `limit` 1~1,000, `offset` 0 이상, 정수·불리언 자료형 확인 |
| 필터 자료형 오류 | 실제 DB 컬럼에 맞는 숫자·문자열·날짜 값인지 확인 |
| 조회 제한 시간 초과 | 필터와 조회 범위를 조정하고 필요한 경우 `DB_STATEMENT_TIMEOUT_MS` 설정 변경 후 재시작 |
| 시작 시 메타데이터 오류 | 두 JSON 파일의 테이블·컬럼 목록, 설명 누락·중복과 도구명 충돌 확인 |

서버는 호출마다 별도 비동기 DB 연결과 읽기 전용 트랜잭션을 사용하고, 조회가 끝나면 연결을 닫습니다. 필터 값은 매개변수로 바인딩합니다. DB 오류의 원문이나 비밀번호는 로그·응답에 포함하지 않으며 서버 로그에는 오류 유형과 SQLSTATE를 기록합니다.

## 9. 검증 방법

개발용 의존성을 설치하고 테스트를 실행합니다.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

기본 테스트는 스키마·컬럼 설명의 일치, 전체 도구 등록, 입력 검증, SQL 구성, 직렬화, 오류 처리와 실제 HTTP 초기화·목록·호출을 확인합니다. 서로 다른 클라이언트 세션과 Host·Origin의 허용·차단도 검증합니다. 실제 DB가 필요한 통합 테스트를 제외한 조회 테스트는 DB 응답을 대체하여 실행합니다.

실제 PostgreSQL 통합 테스트는 `TEST_DATABASE_URL`을 명시한 경우에 실행합니다. **테스트 전용 DB**를 사용합니다. 테스트는 고유한 임시 스키마에 테이블·데이터를 생성한 뒤 해당 스키마를 삭제하므로, 테스트 사용자에게 해당 권한이 필요합니다.

```bash
TEST_DATABASE_URL='postgresql://test_user:test_password@localhost:5432/test_db' \
  python3 -m pytest -q -m integration
```

통합 테스트는 중복·NULL·조건·페이지 조회, 소문자·인용된 대문자 식별자, 실제 숫자·날짜·시각 직렬화, 읽기 전용 트랜잭션을 검증합니다. `TEST_DATABASE_URL`이 없으면 건너뛰며, `.env`의 일반 DB 접속정보를 통합 테스트에 자동으로 사용하지 않습니다.

## 10. Docker로 실행

Docker가 설치되어 있고 Docker 엔진을 사용할 수 있는 환경에서 프로젝트 루트를 빌드 컨텍스트로 사용합니다. Docker를 사용하는 경우 호스트의 Python 가상환경은 필요하지 않습니다.

DB·MCP·Agent를 함께 실행하고 두 HTTP 서버의 컨테이너 내부 포트를 `80`으로 사용할 때는 11절을 따릅니다.

### 이미지 빌드

```bash
docker build -t text-to-sql-mcp .
```

이미지는 참조 파일 `dockerfilke_ref`와 같은 `python:3.10-slim-bookworm`을 기반으로 구성합니다. 한국 시간대(`Asia/Seoul`), UTF-8 환경, `ca-certificates`, `libgomp1`, `tzdata`를 설정하고 Python 의존성을 설치합니다. 작업 디렉터리는 `/app/text_to_sql_mcp`입니다. 이미지의 HTTP 기본 바인딩은 `0.0.0.0:31068`이고 `EXPOSE 80`을 설정합니다.

서버 코드와 두 메타데이터 JSON을 이미지에 포함합니다. DB 접속정보는 컨테이너를 실행할 때
`--env-file .env`로 전달합니다. 소스 코드나 메타데이터를 수정하면 이미지를 다시 빌드합니다.

### 컨테이너 셸에서 HTTP 서버 시작

컨테이너를 시작하면 Bash 셸이 열립니다. `.env.example`을 `.env`로 복사하면
`DB_HOST=text_to_sql_db`와 `DB_PORT=5432`가 설정됩니다. 아래 예시는 DB와 같은
`text-to-sql-network`에 연결하고 호스트의 루프백 포트 `31067`에서 접속할 수 있게 합니다.

```bash
docker run --rm -it --name text_to_sql_mcp --network text-to-sql-network \
  --env-file .env -p 127.0.0.1:31067:31068 text-to-sql-mcp
```

컨테이너 셸에서 다음 명령을 실행합니다.

```bash
python3 server.py
```

`Ctrl+C`를 누르면 MCP가 종료되고 Bash 셸로 돌아옵니다. `exit`로 셸을 닫으면 컨테이너가 종료됩니다.

호스트에서 실행하는 MCP 클라이언트에는 다음과 같이 URL을 등록합니다. 클라이언트에서 Docker 명령을 실행할 필요는 없습니다.

```json
{
  "mcpServers": {
    "postgres-columns-docker": {
      "type": "http",
      "url": "http://127.0.0.1:31067/mcp"
    }
  }
}
```

### LLM 서버와 같은 Docker 네트워크에서 실행

LLM 서버 컨테이너도 `text-to-sql-network`에 연결한 뒤 MCP 클라이언트 URL을
**`http://text_to_sql_mcp:31068/mcp`**로 설정합니다. 이 주소는 예제의 `MCP_ALLOWED_HOSTS`에
포함돼 있습니다. 컨테이너 사이에서만 접속할 경우 위의 `docker run`에서 `-p`를 생략할 수
있습니다. 다른 컨테이너 이름이나 네트워크 별칭을 사용한다면 해당 주소를 `MCP_ALLOWED_HOSTS`에 추가합니다.

```json
{
  "mcpServers": {
    "postgres-columns": {
      "type": "http",
      "url": "http://text_to_sql_mcp:31068/mcp"
    }
  }
}
```

### 컨테이너에서 DB에 연결

컨테이너에서 `localhost`는 해당 컨테이너 자신을 가리킵니다. `DB_HOST`에는 컨테이너에서 접근할 수 있는 PostgreSQL 호스트 주소를 설정합니다.

- 외부 PostgreSQL 서버: `.env`에 해당 서버의 호스트명 또는 IP와 접속 포트를 설정합니다.
- 같은 Docker 네트워크의 PostgreSQL 컨테이너: `--network`로 같은 네트워크를 지정하고 `.env`에 `DB_HOST=text_to_sql_db`, `DB_PORT=5432`를 설정합니다.
- 호스트 PC의 PostgreSQL: Docker Desktop에서는 `host.docker.internal`을 사용할 수 있습니다. Linux Docker Engine에서는 아래처럼 호스트 이름 매핑과 `DB_HOST`를 지정할 수 있습니다.

```bash
docker run --rm -it --env-file .env \
  -p 127.0.0.1:31068:31068 \
  --add-host host.docker.internal:host-gateway \
  -e DB_HOST=host.docker.internal \
  text-to-sql-mcp
```

셸에 접속한 뒤 `python3 server.py`를 실행합니다. PostgreSQL은 컨테이너에서 접근할 수 있는
인터페이스와 인증 설정을 사용해야 합니다. 호스트 포트 `31068`은 이 예시를 실행할 때 비어 있어야 합니다.

### 이미지에 포함된 메타데이터 확인

Docker 환경에서는 다음 명령으로 DB 접속 없이 컬럼 도구 등록을 확인할 수 있습니다.

```bash
docker run --rm text-to-sql-mcp python -c \
  'import asyncio; from column_mcp.server import create_server; print(len(asyncio.run(create_server().list_tools())))'
```

현재 메타데이터 기준 예상 출력은 `1530`입니다. 이 확인은 모듈 로딩과 도구 등록을 검증하며 실제 PostgreSQL 연결은 수행하지 않습니다.

## 11. DB·MCP·Agent 통합 실행

세 컨테이너를 같은 Docker 서버에서 실행합니다. 실행 순서는 **DB → MCP → Agent**입니다. MCP와
Agent는 각각 별도의 터미널에서 시작해 서버 프로세스를 실행한 셸을 열어 둡니다.

| 서비스 | 같은 Docker 네트워크 안에서 접속 | Docker 호스트에서 접속 |
| --- | --- | --- |
| DB | `text_to_sql_db:5432` | `127.0.0.1:31066` (Compose 기본값) |
| MCP | `http://text_to_sql_mcp/mcp` | `http://127.0.0.1:31067/mcp` |
| Agent | `http://text_to_sql_agent/agent/chat` | `http://127.0.0.1:31068/agent/chat` |

외부 클라이언트에서 접속할 때는 MCP와 Agent의 `127.0.0.1`을 서버 주소로 바꿉니다.
MCP를 서버 주소로 직접 호출한다면 그 주소와 공개 포트 `31067`을 `MCP_ALLOWED_HOSTS`에 추가합니다.
`-p` 값은 `호스트_포트:컨테이너_포트` 순서입니다. 같은 네트워크에서는 컨테이너 이름과 내부 포트로
접속하므로 MCP와 Agent의 내부 HTTP 포트 `80`은 URL에서 생략할 수 있습니다.

### 공통 준비

Docker 호스트에 `text-to-sql-network`가 아직 없다면 한 번 생성합니다.

```bash
docker network create text-to-sql-network
```

세 프로젝트 폴더에서 각각 `.env`를 준비합니다. 파일이 없는 경우 해당 폴더 안에서
`cp .env.example .env`를 실행한 뒤 아래 서비스별 값을 적용합니다.

### DB 이미지 빌드 및 PostgreSQL 시작

호스트의 `text_to_sql_db` 폴더에서 실행합니다. Compose가 이미지를 빌드하고
`text_to_sql_db` 컨테이너에서 PostgreSQL을 시작합니다. 빈 데이터 디렉터리로 처음 기동하면
샘플 데이터도 적재합니다.

```bash
docker compose up -d
docker compose ps
```

`db`가 `healthy`로 표시되면 MCP를 시작할 수 있습니다. 데이터를 확인하려면 호스트에서 DB 셸에 접속합니다.

```bash
docker compose exec -u postgres db bash
```

DB 컨테이너 셸에서는 샘플 데이터 검증을 실행할 수 있습니다.

```bash
seed-db verify
```

### MCP 이미지 빌드 및 컨테이너 생성

`text_to_sql_mcp/.env`의 `DB_HOST=text_to_sql_db`, `DB_PORT=5432`를 확인합니다.
`MCP_ALLOWED_HOSTS`의 기존 쉼표 구분 목록에 **`text_to_sql_mcp`**를 추가합니다. Agent가
`http://text_to_sql_mcp/mcp`로 접속할 때 HTTP `Host` 헤더가 `text_to_sql_mcp`로 전달되기 때문입니다.

호스트의 `text_to_sql_mcp` 폴더에서 이미지를 빌드한 뒤 컨테이너를 생성합니다. `--env-file .env`가
호스트의 DB 접속 설정을 컨테이너에 전달하고 `-it`가 Bash 셸을 엽니다.

```bash
docker build --tag text-to-sql-mcp .
docker run --shm-size="128G" --memory="256G" --cpus=16 \
  -p 31067:80 --network text-to-sql-network --name text_to_sql_mcp \
  --env-file .env -it text-to-sql-mcp
```

MCP 컨테이너 셸에서 내부 포트 `80`으로 서버를 시작합니다. 호스트의 `31067`이 이 포트에 연결됩니다.

```bash
MCP_PORT=80 python3 server.py
```

### Agent 이미지 빌드 및 컨테이너 생성

`text_to_sql_agent/.env`에서 `MCP_URL`을 다음 값으로 설정하고, 사용할 LLM 공급자의 접속 설정을 준비합니다.

```dotenv
MCP_URL=http://text_to_sql_mcp/mcp
```

호스트의 `text_to_sql_agent` 폴더에서 이미지를 빌드하고 컨테이너를 생성합니다.

```bash
docker build --tag text-to-sql-agent .
docker run --shm-size="128G" --memory="256G" --cpus=16 \
  -p 31068:80 --network text-to-sql-network --name text_to_sql_agent \
  --env-file .env -it text-to-sql-agent
```

Agent 컨테이너 셸에서 내부 포트 `80`으로 API를 시작합니다. 호스트의 `31068`이 이 포트에 연결됩니다.

```bash
python3 main.py --port 80
```

### Agent API 확인

Docker 호스트의 별도 터미널에서 Agent 서버와 `/agent/chat` 호출을 확인합니다.

```bash
curl --fail-with-body http://127.0.0.1:31068/health

curl --fail-with-body http://127.0.0.1:31068/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"FCLT_ORG_DLC_KEY 테이블의 DLC_KEY 값 3개를 조회해줘.","provider":"local"}'
```

`/health`는 Agent 프로세스의 응답을 확인합니다. `/agent/chat`은 설정한 LLM과 MCP를 통해 질의를
처리하고 `data.answer`와 `data.tool_calls`를 반환합니다. OpenAI 또는 OpenRouter로 실행할 때는
`provider`를 `openai` 또는 `openrouter`로 지정합니다.
