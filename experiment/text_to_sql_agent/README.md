# LLM·MCP 데이터 조회 에이전트

자연어 질문 → 선택한 LLM → MCP 도구 검색·호출 → 최종 답변의 흐름을 제공하는 독립 FastAPI 서버입니다.
로컬 OpenAI 호환 서버, OpenAI, OpenRouter를 지원하며 `text_to_sql_api`의 모델 기본값과
공급자별 호출 옵션을 따릅니다. 기존 API 서버를 실행하거나 그 Python 패키지를 설치할 필요는 없습니다.

## 설치와 실행

Python 3.10 이상을 사용합니다. 아래 명령은 `text_to_sql_agent/`에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# .env에 사용할 공급자의 접속 정보와 키를 입력합니다.
python main.py
```

기본 주소는 `http://127.0.0.1:31067`, OpenAPI 문서는 `/docs`입니다.
`python main.py --host 0.0.0.0 --port 31067`로 바인딩을 바꿀 수 있습니다.
`uvicorn main:app --host 127.0.0.1 --port 31067`로 실행할 수도 있습니다.

자체 프로젝트 루트의 `.env`만 읽으며 프로세스 환경변수가 우선합니다. 설정을 읽을 때 프로세스
환경변수를 수정하지 않습니다. 작업 디렉터리가 달라도 같은 `.env`를 읽습니다.
`text_to_sql_api/.env`는 자동 공유하지 않으므로 같은 모델을 사용하려면 필요한 값을 직접 옮기세요.
실제 API 키는 소스나 예제 파일에 넣지 않습니다.

| 설정 | 기본값 / 설명 |
| --- | --- |
| `AGENT_HOST` / `AGENT_PORT` | `127.0.0.1` / `31067` |
| `MCP_URL` | `http://127.0.0.1:31068/mcp` |
| `LLM_API_BASE` | 로컬 모델의 OpenAI 호환 `/v1` 주소. 기존 API의 같은 설정값 사용 가능 |
| `LLM_MODEL_NAME` | `DeepSeek-V4-Flash-0731` |
| `OPENAI_API_KEY` | OpenAI 선택 시 필수 |
| `OPENAI_LLM_MODEL_NAME` | `gpt-6-luna` |
| `OPENROUTER_API_KEY` | OpenRouter 선택 시 필수 |
| `OPENROUTER_LLM_MODEL_NAME` | `deepseek/deepseek-v4-flash-0731` |
| `AGENT_MAX_LLM_ROUNDS` | `12`. 최종 답변을 포함한 LLM 호출 횟수 |
| `AGENT_MAX_TOOL_CALLS` | `32`. 검색과 실제 조회를 합한 함수 호출 횟수 |
| `AGENT_REQUEST_TIMEOUT_SECONDS` | `180`. 연결·재시도·도구 실행을 포함한 전체 제한 시간 |
| `LLM_TIMEOUT_SECONDS` / `MCP_TIMEOUT_SECONDS` | 각각 `60`초 |

공급자 설정은 해당 요청에서만 검증합니다. 예를 들어 OpenRouter만 설정해도 서버를 시작하고
OpenRouter 요청을 처리할 수 있습니다. 로컬 모델은 기존 API와 동일하게 `dummy` 키와
`temperature=0`을 사용하며 DeepSeek 모델의 thinking을 비활성화합니다. OpenAI는 공식
`https://api.openai.com/v1`을 사용하고 기본 모델에서 `reasoning_effort=none`을 적용합니다.
OpenRouter는 `https://openrouter.ai/api/v1`, `temperature=0`, reasoning 비활성화,
`provider.require_parameters=true`를 사용합니다. OpenRouter 모델의 공급자 접두사는 유지합니다.
LLM SDK의 재시도는 최대 2회이며 전체 요청 제한 시간 내에서 수행됩니다.

## MCP와 DB 연결

에이전트는 실행 중인 `text_to_sql_mcp`의 Streamable HTTP `/mcp`에 연결합니다.
DB 접속은 MCP가 담당하며 에이전트에는 DB 비밀번호나 PostgreSQL 드라이버가 필요하지 않습니다.
`text_to_sql_db`를 실행·초기화한 뒤 MCP의 `.env`에 같은 DB 접속 정보를 설정하세요.

이 저장소의 DB 예제 설정을 사용한다면 호스트에서 실행하는 MCP의 설정은 아래와 같습니다.
`DB_PASSWORD`에는 DB 초기화에 실제 사용한 값을 입력합니다.

```dotenv
DB_HOST=127.0.0.1
DB_PORT=31066
DB_NAME=beaver_db
DB_USER=minkyo
DB_PASSWORD=<DB 초기화에 사용한 비밀번호>
DB_SCHEMA=public
DB_IDENTIFIER_CASE=lower
```

기존 PostgreSQL 볼륨의 계정·DB명·비밀번호는 `.env` 변경만으로 바뀌지 않습니다.
MCP의 설정과 실제 DB 계정이 일치해야 합니다. 에이전트는 DB나 MCP 프로세스를 시작하지 않습니다.

## HTTP API

`GET /health`는 `{"status":"ok"}`를 반환합니다. LLM·MCP·DB 연결 검사는 수행하지 않습니다.

`POST /agent/chat`:

```json
{
  "query": "조직별 DLC 키 중 앞의 5개를 보여줘",
  "history": [],
  "provider": "local",
  "table_name": ["FCLT_ORG_DLC_KEY"]
}
```

`provider`는 `local`(기본), `openai`, `openrouter` 중 하나입니다. 모델은 환경변수로 선택합니다.
`history`는 오래된 순서의 `{"role":"user"|"assistant","content":"..."}` 배열이며 최근
메시지 3개만 전달합니다. 현재 `query`는 별도로 추가합니다. 이력은 서버에 저장하지 않습니다.
`table_name`은 조회할 테이블명의 배열입니다. MCP 카탈로그의 테이블명과 대소문자까지 일치하는 도구만
검색하고 호출합니다. 필드를 생략하면 전체 도구를 사용할 수 있습니다. 빈 배열을 보내거나 일치하는
테이블이 없으면 검색 결과는 비어 있으며 호출 가능한 도구도 없습니다.

```bash
curl -sS http://127.0.0.1:31067/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"DLC_KEY 값을 5개 조회해줘","provider":"local"}'
curl -sS http://127.0.0.1:31067/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"DLC_KEY 값을 5개 조회해줘","provider":"openai"}'
curl -sS http://127.0.0.1:31067/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"DLC_KEY 값을 5개 조회해줘","provider":"openrouter"}'
```

응답 형식(값은 설명용 예시):

```json
{
  "status": "success",
  "msg": "success",
  "data": {
    "answer": "조회한 DLC 키는 DLC001입니다. 전체 데이터 중 일부입니다.",
    "tool_calls": [{
      "id": "call_example",
      "function_name": "call_mcp_tool",
      "mcp_tool_name": "get_fclt_org_dlc_key_dlc_key",
      "arguments": {"name":"get_fclt_org_dlc_key_dlc_key","arguments":{"limit":1}},
      "result": {
        "content": [{"type":"text","text":"..."}],
        "structuredContent": {"table":"FCLT_ORG_DLC_KEY","column":"DLC_KEY","values":["DLC001"],"returned_count":1,"limit":1,"offset":0,"distinct":false,"has_more":true},
        "isError": false
      },
      "is_error": false
    }]
  },
  "meta_data": [{"request_id":"example","provider":"local","model_name":"DeepSeek-V4-Flash-0731","llm_calls":3,"question_token":100,"answer_token":30}],
  "elapsed_time": 1.2
}
```

호출 내역에는 검색도 포함됩니다. `result`는 MCP의 `content`, `structuredContent`, `isError`를
보존하며 복구 가능한 함수 인자 오류에는 `{"error":"..."}`가 들어갑니다. 모델 토큰 사용량은
모든 완료된 LLM 호출의 합입니다. 공급자가 사용량을 생략하면 해당 호출은 0으로 집계합니다.
중간 추론 내용은 응답이나 로그에 포함하지 않습니다.

| HTTP 상태 | 의미 |
| --- | --- |
| `200` | 최종 답변 생성 완료. 조회 실패나 기능 한계를 설명하는 답변일 수도 있으므로 호출 내역도 확인 |
| `422` | 잘못된 요청 형식·공급자·빈 질문 |
| `500` | 선택한 공급자의 필수 설정 누락 또는 잘못된 설정 |
| `502` | LLM/MCP 연결·응답 오류, 도구 호출 미지원 또는 반복 횟수 한도 초과 |
| `504` | 전체 요청 제한 시간 초과 |

오류 응답도 같은 구조를 사용하며 `data.answer`는 `null`, `data.tool_calls`는 중단 전까지의
기록입니다. 요청 형식 검증 오류는 `data=null`입니다. API 오류 메시지는 인증 원문을 노출하지 않습니다.

## 도구 처리와 데이터 범위

매 요청마다 독립 MCP 세션을 열어 전체 도구 목록(페이지 포함)을 읽습니다. 1,530개 도구 전체를
LLM 함수 목록에 넣는 대신 `search_mcp_tools`와 `call_mcp_tool` 두 함수로 연결합니다.
검색은 이름·설명의 대소문자를 무시하는 토큰 OR 부분 일치이며 일치 토큰 수 내림차순,
도구명 오름차순입니다. 빈 검색어는 목록 탐색입니다. 기본 10개, 최대 50개를 반환합니다.
인자는 MCP의 입력 JSON Schema로 검증하고 등록된 도구만 호출합니다. 오류는 LLM에 반환해
정정할 수 있게 하며 호출 횟수 제한은 검색·실패 호출에도 적용합니다.

MCP의 현재 기능은 단일 컬럼 조회, 같은 테이블의 일치 필터, 중복 제거, 페이지 조회입니다.
`has_more`는 그대로 전달하며 자동 전체 조회는 하지 않습니다. 서로 다른 컬럼은 독립 정렬되므로
같은 배열 인덱스를 같은 DB 행으로 연결할 수 없습니다. 임의 SQL·JOIN 실행이나 범용 집계는
지원하지 않습니다. 프롬프트는 조회 근거와 부분 결과를 명시하도록 지시하지만 최종 자연어 답변은
모델 출력이므로 원본 호출 내역과 함께 확인할 수 있습니다. 네이티브 도구 호출을 지원하는 LLM이
필요하며, 도구 미지원 모델을 위한 JSON 텍스트 파싱 대체 경로는 제공하지 않습니다.

## Docker

컨테이너를 시작하면 Bash 셸이 열립니다. 아래 예시는 호스트와 컨테이너의 `31067`을 연결하고
`text-to-sql-network`에 연결된 MCP 서버를 사용합니다.

```bash
docker build -t text-to-sql-agent .
docker run --rm -it --env-file .env \
  -e AGENT_HOST=0.0.0.0 \
  -e MCP_URL=http://text_to_sql_mcp:31068/mcp \
  --network text-to-sql-network \
  -p 127.0.0.1:31067:31067 text-to-sql-agent
```

컨테이너 셸에서 Agent 서버를 시작합니다.

```bash
python3 main.py
```

컨테이너의 `127.0.0.1`은 컨테이너 자신을 뜻합니다. 에이전트의 `MCP_URL`은 MCP 컨테이너 주소,
MCP의 `DB_HOST`는 DB 컨테이너 주소여야 합니다. 같은 네트워크에서 DB를 접속할 때는
호스트 공개 포트 `31066` 대신 DB 컨테이너 내부 포트 `5432`를 사용합니다.
MCP 컨테이너는 `MCP_HOST=0.0.0.0`으로 실행하고 접근 주소를 MCP 허용 Host 목록에 넣으세요.
이미지에 `.env`는 복사하지 않습니다.

## 테스트

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

실제 HTTP로 동작하는 테스트 MCP와 모의 LLM을 사용합니다. 유료 LLM API나 실제 DB는
호출하지 않습니다. 세 공급자 설정, SDK 요청·함수 결과 메시지, MCP 목록 페이지, 도구 검색·검증,
오류 복구, 호출 한도·취소·시간 초과, 동시 요청 세션 격리와 연결 종료를 검증합니다.
