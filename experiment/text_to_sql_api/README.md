# Text-to-SQL 스키마 인덱싱·검색·RDF triplet·SQL 생성 API

`reference_code/advanced-checkmate`와 같은 FastAPI 애플리케이션 팩터리, 서비스 구성, 의존성 주입 구조를 사용합니다. `dataset/db_schema.json`의 신규 테이블을 FAISS 인덱스로 관리하고, 자연어 질의로 관련 테이블을 검색한 뒤 검색 결과와 대화 히스토리를 이용하여 PostgreSQL SQL을 생성합니다. 전체 카탈로그를 LLM에 직접 전달하거나 RDF triplet 관계를 확장하여 스키마를 검색할 수도 있습니다. 전체 스키마와 설명으로 테이블 간 관계를 RDF triplet 형태의 JSON으로 생성·저장하는 API도 제공합니다.

## 설치 및 실행

```bash
cd text_to_sql_api
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 main.py --host 0.0.0.0 --port 31065
```

또는 프로젝트 디렉터리에서 `uvicorn main:app --host 0.0.0.0 --port 31065`으로 실행할 수 있습니다. API 문서는 `http://localhost:31065/docs`에서 확인합니다. Python 3.10 이상과 Linux의 `fcntl` 파일 잠금을 사용합니다.

`python main.py`는 `--port` → 프로세스의 `API_PORT` → `.env`의 `API_PORT` → 기본값 `31065`
순서로 수신 포트를 결정합니다. 포트는 `1~65535` 범위의 정수여야 합니다.
`uvicorn main:app`으로 직접 실행할 때는 Uvicorn의 `--port` 옵션을 지정합니다.

## Docker 실행

Docker 빌드 컨텍스트는 `text_to_sql_api` 디렉터리입니다. 참조 프로젝트의 의존성 설치·소스 복사 순서를 따르며, 현재 API는 외부 서버에서 모델을 호출하므로 CPU용 `python:3.10-slim-bookworm` 이미지를 사용합니다. 인증서, 시간대, OpenMP 라이브러리를 설치하고 컨테이너의 기본 명령으로 `/bin/bash`를 실행합니다. `docker run -it`로 Bash에 접속한 뒤 API 서버를 직접 시작할 수 있습니다. 이미지 이름 뒤에 다른 명령을 지정하면 기본 Bash 대신 해당 명령을 실행합니다.

Docker에서도 `requirements.txt`의 버전 고정 목록으로 의존성을 직접 설치합니다. 로컬 설치와 개발용 의존성 설치 명령도 같은 목록을 사용합니다.

Dockerfile은 `EXPOSE 80`을 선언하지만 실제 HTTP 수신 포트는 기본 `31065`입니다.
호스트 공개는 아래 예제처럼 `-p 31065:31065`로 설정합니다.

이미지 빌드 전에 `.env.example`을 `.env`로 복사하고 API 서버 URL과 필요한 OpenAI 키를 설정합니다. 프로젝트 루트의 `.env`는 Dockerfile의 `COPY . .`을 통해 이미지의 `/app/text_to_sql_api/.env`에 포함됩니다.

```bash
cd text_to_sql_api
docker build -t text-to-sql-api:latest .
```

`langchain-openai==1.1.12`는 `openai>=2.26.0,<3.0.0`을 요구하므로 SDK를 `openai==2.26.0`으로 고정합니다. 이전 `openai==2.24.0`과는 의존성 조건이 충돌합니다. 이 조합은 Python 3.10을 지원합니다.

설치 중 `pip is looking at multiple versions ... This could take a while.`은 호환 버전을 탐색하는 안내입니다. 빌드가 실패하면 뒤에 나오는 `ERROR: Cannot install`과 `ResolutionImpossible` 등의 실제 오류를 확인하세요. 상세 로그는 다음 명령으로 확인할 수 있습니다.

```bash
docker build --progress=plain --tag text-to-sql-api .
```

애플리케이션은 이미지에 포함된 `.env`를 읽습니다. 다음 명령은 컨테이너의 Bash에 접속하며, 임베딩·triplet 저장 경로를 명시하고 호스트 디렉터리를 연결하여 컨테이너 삭제 후에도 데이터를 보존합니다.

```bash
mkdir -p embedded_schema embedded_schema_open_ai embedded_triplet embedded_triplet_open_ai rdf_triplet rdf_triplet_open_ai rdf_triplet_open_router
docker run -it --name text-to-sql-api \
  -e EMBEDDED_SCHEMA=/app/text_to_sql_api/embedded_schema \
  -e EMBEDDED_SCHEMA_OPEN_AI=/app/text_to_sql_api/embedded_schema_open_ai \
  -e EMBEDDED_TRIPLET=/app/text_to_sql_api/embedded_triplet \
  -e EMBEDDED_TRIPLET_OPEN_AI=/app/text_to_sql_api/embedded_triplet_open_ai \
  -e RDF_TRIPLET=/app/text_to_sql_api/rdf_triplet \
  -e RDF_TRIPLET_OPEN_AI=/app/text_to_sql_api/rdf_triplet_open_ai \
  -e RDF_TRIPLET_OPEN_ROUTER=/app/text_to_sql_api/rdf_triplet_open_router \
  -p 31065:31065 \
  --mount "type=bind,source=$(pwd)/embedded_schema,target=/app/text_to_sql_api/embedded_schema" \
  --mount "type=bind,source=$(pwd)/embedded_schema_open_ai,target=/app/text_to_sql_api/embedded_schema_open_ai" \
  --mount "type=bind,source=$(pwd)/embedded_triplet,target=/app/text_to_sql_api/embedded_triplet" \
  --mount "type=bind,source=$(pwd)/embedded_triplet_open_ai,target=/app/text_to_sql_api/embedded_triplet_open_ai" \
  --mount "type=bind,source=$(pwd)/rdf_triplet,target=/app/text_to_sql_api/rdf_triplet" \
  --mount "type=bind,source=$(pwd)/rdf_triplet_open_ai,target=/app/text_to_sql_api/rdf_triplet_open_ai" \
  --mount "type=bind,source=$(pwd)/rdf_triplet_open_router,target=/app/text_to_sql_api/rdf_triplet_open_router" \
  text-to-sql-api:latest
```

위 `-e` 옵션은 이미지에 포함된 `.env`의 저장 경로보다 우선합니다. 실행 시 설정을 덮어쓰려면 `docker run`에 `--env-file ./other.env` 또는 `-e` 옵션을 추가할 수 있습니다. 호스트의 `.env` 변경을 이미지에 반영하려면 이미지를 재빌드하고 새 이미지로 컨테이너를 생성해야 합니다.

저장 위치를 변경하려면 환경변수의 컨테이너 경로와 해당 마운트의 `target`을 함께 변경합니다. 기존 호스트 인덱스가 있으면 마운트된 데이터를 사용하며, 빈 디렉터리에서는 인덱싱 엔드포인트를 먼저 호출합니다. 기본 입력 파일인 `dataset/db_schema.json`은 이미지에 포함됩니다.

컨테이너의 Bash에서 다음 명령으로 API 서버를 시작합니다.

```bash
python main.py --host 0.0.0.0 --port 31065
```

서버를 실행한 상태에서 다른 터미널을 열어 서버 시작과 25개 엔드포인트 등록을 외부 모델 호출 없이 확인할 수 있습니다.

```bash
docker logs text-to-sql-api
curl --fail --retry 10 --retry-connrefused --retry-delay 1 \
  http://localhost:31065/openapi.json
```

API 문서는 `http://localhost:31065/docs`에서 확인합니다. `.dockerignore`는 `.env.*` 변형 파일, 가상환경, 캐시, 테스트 결과와 기존 임베딩·triplet 데이터를 빌드 컨텍스트에서 제외하며, `.env`와 `.env.example`은 포함합니다. Docker 실행 도구가 없는 개발 환경에서는 실제 이미지 빌드·실행 검증을 수행할 수 없으므로, Docker가 설치된 환경에서 위 명령으로 확인합니다.

## 환경 설정

프로젝트 루트의 `.env`를 사용합니다. 새 환경에서는 `.env.example`을 `.env`로 복사합니다.

```dotenv
API_PORT=31065
EMBEDDED_SCHEMA=./embedded_schema
EMBEDDED_TRIPLET=./embedded_triplet
EMBEDDED_TRIPLET_OPEN_AI=./embedded_triplet_open_ai
EMBEDDING_API_BASE=https://aidev.lotte.net/api/fo/lcloud/api/embed/v1
LLM_API_BASE=https://lit-tvjd8b216.tail0f72bf.ts.net/v1
LLM_MODEL_NAME=DeepSeek-V4-Flash-0731
OPENAI_API_KEY=
OPENAI_EMBEDDING_MODEL_NAME=text-embedding-3-small
OPENAI_LLM_MODEL_NAME=gpt-6-luna
EMBEDDED_SCHEMA_OPEN_AI=./embedded_schema_open_ai
RDF_TRIPLET=./rdf_triplet
RDF_TRIPLET_OPEN_AI=./rdf_triplet_open_ai
```

- `EMBEDDED_SCHEMA`: 인덱스를 저장하는 루트 디렉터리입니다. 상대 경로는 실행 위치와 관계없이 `text_to_sql_api` 기준입니다.
- `EMBEDDED_TRIPLET`: triplet 임베딩 인덱스의 루트 디렉터리입니다. 기본값은 `./embedded_triplet`이며 실제 스냅샷은 그 아래 `triplets/`에 저장합니다. 상대 경로는 프로젝트 루트 기준입니다.
- `EMBEDDED_TRIPLET_OPEN_AI`: OpenAI triplet 임베딩 인덱스의 루트 디렉터리입니다. 기본값은 `./embedded_triplet_open_ai`이며 실제 스냅샷은 그 아래 `triplets/`에 저장합니다. 상대 경로는 프로젝트 루트 기준이며 `EMBEDDED_TRIPLET`과 다른 경로여야 합니다.
- `RDF_TRIPLET`: 생성한 관계를 저장하고 읽는 루트 디렉터리입니다. 기본값은 `./rdf_triplet`이며 파일은 그 아래 `triplets.json`입니다. 상대 경로는 `text_to_sql_api` 기준입니다.
- `RDF_TRIPLET_OPEN_AI`: OpenAI로 생성한 관계의 저장·로딩 디렉터리입니다. 기본값은 `./rdf_triplet_open_ai`이며 파일은 그 아래 `triplets.json`입니다. `RDF_TRIPLET`과 같은 경로로 설정할 수 없습니다.
- `EMBEDDING_API_BASE`: OpenAI 호환 임베딩 API의 기본 URL입니다. 끝에 `/embeddings`를 붙이지 않습니다. 기본 URL은 참조 프로젝트와 같습니다.
- 기존 프로세스 환경변수가 `.env`보다 우선합니다. 설정 변경 후 서버를 재시작합니다.
- 임베딩 모델은 참조와 같은 `BAAI/bge-m3`입니다. `--embedding_model_name`으로 변경할 수 있으며 기존 인덱스와 모델이 다르면 409를 반환합니다.
- 입력 파일 기본값은 프로젝트 기준 `dataset/db_schema.json`이며 `--dataset_path`로 변경할 수 있습니다.
- Triplet 관계 기반 스키마 검색 엔드포인트의 최대 탐색 깊이는 요청 JSON의 `max_hop`으로 지정합니다. `/retrieve/triplet_based_schema`, `/retrieve/triplet_based_schema_open_ai`, `/retrieve/triplet_based_schema_open_router`, `/retrieve/only_triplet_based_schema`, `/retrieve/only_triplet_based_schema_open_ai`, `/retrieve/only_triplet_based_schema_open_router`, `/retrieve/hybrid_only_based_schema`, `/retrieve/hybrid_only_based_schema_open_ai`에 적용됩니다. 생략하면 기본값 `3`을 사용합니다. `0`은 시작 테이블만 사용하며, 양수는 지정한 hop 수만큼 탐색합니다. 요청마다 독립적으로 적용됩니다.
- `max_hop=-1`은 최대 10-hop까지 탐색합니다. 방문한 테이블은 다시 확장하지 않고 다른 미방문 테이블의 탐색은 계속합니다. 다음 단계에서 새 테이블이 없으면 조기 종료합니다. `-1` 외의 음수와 정수가 아닌 값은 허용하지 않습니다.
- LLM으로 시작점을 선택하는 여섯 triplet 검색 엔드포인트는 요청의 `top_k`로 시작 테이블 수를 제한합니다. 기본값 `-1`은 무제한이고 양수는 최대 k개입니다. LLM 응답의 이름 검증과 중복 제거 후 적용하며 최종 반환 수에는 적용하지 않습니다. `/retrieve/hybrid_only_based_schema`와 `/retrieve/hybrid_only_based_schema_open_ai`는 같은 제한을 임베딩 검색으로 선택한 시작점에 적용합니다.
- `LLM_API_BASE`, `LLM_MODEL_NAME`: SQL·triplet 생성과 LLM·triplet 기반 스키마 검색에 사용할 OpenAI 호환 LLM의 기본 URL과 모델입니다. 초기값은 참조 프로젝트와 같습니다. URL 끝에 `/chat/completions`를 붙이지 않습니다.
- LLM 설정 우선순위는 `--llm_api_base`·`--model_name` 실행 인자, 프로세스 환경변수, `.env`, 모델 기본값 순서입니다. 모델 기본값은 `DeepSeek-V4-Flash-0731`이며 URL은 설정해야 합니다.
- LLM 설정은 SQL·triplet 생성 및 LLM·triplet 기반 검색 요청에서 검증하므로 설정이 없어도 인덱싱·임베딩 검색 엔드포인트를 사용할 수 있습니다. LLM 서버는 JSON Schema 구조화 응답을 지원해야 합니다. 참조처럼 API 키는 `dummy`로 전달합니다.

```bash
python3 main.py --llm_api_base https://llm.example.com/v1 --model_name your-model
```

## OpenAI 전용 엔드포인트

OpenAI API 키를 사용하는 POST 엔드포인트 10개를 제공합니다. 요청·응답 형식과 기능은 대응하는 기존 엔드포인트와 같으며 클라이언트와 모델, 임베딩·triplet 저장 경로를 별도로 구성합니다. LLM·triplet 기반 검색은 OpenAI 전용 요청·응답 모델을 정의합니다.

| 엔드포인트 | 입력 | 반환 |
| --- | --- | --- |
| `/retrieve/indexing_schema_open_ai` | 요청 본문 없음 | 신규 테이블 인덱싱 결과 |
| `/retrieve/indexing_triplet_open_ai` | 요청 본문 없음 | 신규 triplet 인덱싱 결과 |
| `/retrieve/embedding_based_schema_open_ai` | `query`, `top_k`, 선택 `history` | `data`에 이름·DDL·설명의 세 리스트 |
| `/retrieve/embedded_triplet_based_schema_open_ai` | `query`, `top_k`, 선택 `history` | Triplet 임베딩으로 검색한 상위 테이블의 이름·DDL·설명 리스트 |
| `/retrieve/llm_based_schema_open_ai` | `query`, 선택 `history` | 전체 카탈로그에서 LLM으로 선택한 이름·DDL·설명의 세 리스트 |
| `/retrieve/triplet_based_schema_open_ai` | `query`, 선택 `history`, `max_hop`, `top_k` | RDF 관계와 LLM으로 선택한 이름·DDL·설명의 세 리스트 |
| `/retrieve/only_triplet_based_schema_open_ai` | `query`, 선택 `history`, `max_hop`, `top_k` | OpenAI로 시작 테이블을 선정하고 관계를 확장한 전체 후보의 이름·DDL·설명 리스트 |
| `/retrieve/hybrid_only_based_schema_open_ai` | `query`, 선택 `history`, `max_hop`, `top_k` | OpenAI triplet 임베딩으로 시작점을 선택하고 RDF로 확장한 전체 후보의 이름·DDL·설명 리스트 |
| `/generate_sql/llm_based_generate_sql_open_ai` | `query`, `schema`, 선택 `history` | `data`에 PostgreSQL SQL 문자열 |
| `/retrieve/generate_triplet_open_ai` | 요청 본문 없음 | `data`에 테이블 관계 triplet 목록 |

프로젝트 `.env`의 `OPENAI_API_KEY=`에 실제 발급받은 키를 입력한 뒤 서버를 재시작합니다. 키는 서버 설정에서 읽으며 요청 본문으로 전달하지 않습니다. 프로세스 환경변수가 `.env`보다 우선합니다. `.env.example`의 키 값은 비워 둡니다.

```dotenv
OPENAI_API_KEY=
OPENAI_EMBEDDING_MODEL_NAME=text-embedding-3-small
OPENAI_LLM_MODEL_NAME=gpt-6-luna
EMBEDDED_SCHEMA_OPEN_AI=./embedded_schema_open_ai
EMBEDDED_TRIPLET_OPEN_AI=./embedded_triplet_open_ai
RDF_TRIPLET_OPEN_AI=./rdf_triplet_open_ai
```

- OpenAI 클라이언트는 공식 주소 `https://api.openai.com/v1`에 연결하고 해당 키로 Bearer 인증합니다.
- 인덱싱과 임베딩 기반 검색은 같은 `text-embedding-3-small` 모델을 사용합니다. 기본 임베딩 차원은 1536입니다.
- Hybrid 검색은 OpenAI 임베딩으로 시작점을 선택하고 OpenAI용 RDF를 확장하며 `OPENAI_LLM_MODEL_NAME` 설정은 필요하지 않습니다.
- SQL·triplet 생성과 LLM·triplet 기반 검색은 `OPENAI_LLM_MODEL_NAME`을 사용하며 기본 모델은 `gpt-6-luna`입니다. 해당 모델에는 `reasoning_effort="none"`을 적용하고 각 기능의 구조화 응답 모델을 사용합니다. SQL 생성 문법은 PostgreSQL입니다.
- 검색과 SQL 생성은 기존처럼 최근 `history[-3:]`과 현재 질의를 사용합니다. 메시지 하나가 1 turn입니다.
- OpenAI 설정은 해당 엔드포인트 호출 시 검증하므로 키가 없어도 기존 엔드포인트와 서버 시작은 가능합니다.
- 키는 설정 객체의 문자열 표현에서 제외하고, OpenAI 오류 로그·응답에 포함된 키 값은 `[REDACTED]`로 처리합니다.

임베딩 저장 구조는 다음과 같습니다. 상대 경로는 `text_to_sql_api` 기준입니다.

```text
embedded_schema_open_ai/
└── db_schema/
    ├── index.faiss
    ├── metadata.json
    └── raw_data.json
```

OpenAI 스키마 인덱싱은 이 디렉터리의 `table_name`을 기준으로 신규 항목만 추가합니다. 기존 `embedded_schema/`에 같은 테이블이 있어도 OpenAI용으로 따로 임베딩합니다. OpenAI 임베딩 검색은 OpenAI 전용 디렉터리를 읽으며 기존 인덱스를 대체 사용하지 않습니다. 두 저장 경로를 같게 설정하면 500 오류를 반환합니다. 임베딩 모델을 변경하여 기존 인덱스와 모델·차원이 다르면 409 오류를 반환합니다. LLM 기반 검색은 원본 카탈로그만 사용하며 임베딩·RDF 저장소를 사용하지 않습니다.

OpenAI triplet 인덱싱은 `<RDF_TRIPLET_OPEN_AI>/triplets.json`을 읽고 `<EMBEDDED_TRIPLET_OPEN_AI>/triplets/`에 저장합니다. 일반 인덱싱은 `<RDF_TRIPLET>/triplets.json`을 우선 사용하고, 파일이 없을 때만 `<RDF_TRIPLET_OPEN_ROUTER>/triplets.json`을 읽습니다. 두 임베딩 인덱스에는 같은 관계 ID가 있어도 각각의 모델 벡터를 저장하며 기존 `EMBEDDED_TRIPLET`의 데이터를 대체하지 않습니다.

OpenAI triplet은 `rdf_triplet_open_ai/triplets.json`에 저장합니다. `RdfTripletStore(resolve_project_path(settings.rdf_triplet_open_ai)).load()`로 같은 목록을 읽을 수 있습니다. 기존 `rdf_triplet/triplets.json`과 독립적으로 관리하며, `/retrieve/triplet_based_schema_open_ai`가 이 파일을 사용합니다. 임베딩 검색과 SQL 생성은 이 파일을 직접 읽지 않습니다.

인덱싱 후 검색을 호출하고, 검색 응답의 `data`를 SQL 생성 요청의 `schema`로 전달할 수 있습니다. 기존 테스트 요청 JSON으로 각 엔드포인트를 개별 호출하는 예시입니다.

```bash
curl -X POST http://localhost:31065/retrieve/indexing_schema_open_ai

curl -X POST http://localhost:31065/retrieve/embedding_based_schema_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/embedding_based_schema.json

curl -X POST http://localhost:31065/generate_sql/llm_based_generate_sql_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/llm_based_generate_sql.json

curl -X POST http://localhost:31065/retrieve/generate_triplet_open_ai
```

입력 검증 오류는 기본 422 응답입니다. 키·모델 누락과 경로 충돌은 500, OpenAI 호출 실패·인증 실패·요청 한도 오류는 502의 공통 오류 응답을 반환합니다. 검색 인덱스 미생성은 404, 인덱스 손상·모델 또는 차원 불일치는 409입니다. Triplet 기반 검색은 RDF 미생성이면 404, RDF 손상·원본에 없는 테이블 참조이면 409를 반환합니다. 키 누락은 파일 읽기·저장이나 외부 호출 전에 확인합니다.

로그는 `[indexing_schema_open_ai]`, `[indexing_triplet_open_ai]`, `[embedding_based_schema_open_ai]`, `[llm_based_schema_open_ai]`, `[triplet_based_schema_open_ai]`, `[llm_based_generate_sql_open_ai]`, `[generate_triplet_open_ai]`로 구분합니다. 기존 엔드포인트는 기존 서버·모델·인증과 저장 경로를 사용합니다.

## OpenRouter 전용 엔드포인트

OpenRouter로 DeepSeek-V4-Flash-0731을 호출하는 POST 엔드포인트 다섯 개를 제공합니다. 기존 입력·응답 구조와 검색·생성 흐름을 재사용합니다.

| 엔드포인트 | 입력 | 반환 |
| --- | --- | --- |
| `/retrieve/generate_triplet_open_router` | 요청 본문 없음 | 생성한 테이블 관계 triplet 목록 |
| `/generate_sql/llm_based_generate_sql_open_router` | `query`, `schema`, 선택 `history` | `data`에 PostgreSQL SQL 문자열 |
| `/retrieve/llm_based_schema_open_router` | `query`, 선택 `history` | 전체 카탈로그에서 선택한 원본 테이블 스키마 |
| `/retrieve/only_triplet_based_schema_open_router` | `query`, 선택 `history`, `top_k`, `max_hop` | 시작점을 포함한 전체 RDF 확장 스키마 |
| `/retrieve/triplet_based_schema_open_router` | `query`, 선택 `history`, `top_k`, `max_hop` | RDF 확장 후 OpenRouter로 최종 재선별한 원본 스키마 |

프로젝트 `.env`에 아래 설정을 추가합니다. API 키 값은 사용자가 입력하며 서버를 재시작하면 반영됩니다. 프로세스 환경변수가 `.env`보다 우선하고 키를 요청 본문으로 전달하지 않습니다. `.env.example`의 API 키는 빈 값입니다.

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_LLM_MODEL_NAME=deepseek/deepseek-v4-flash-0731
RDF_TRIPLET_OPEN_ROUTER=./rdf_triplet_open_router
```

`OPENROUTER_LLM_MODEL_NAME`에는 공급자 접두사가 포함된 OpenRouter 모델 ID를 저장합니다. 기본값 `deepseek/deepseek-v4-flash-0731`은 요청한 DeepSeek-V4-Flash-0731에 해당합니다. [OpenRouter 모델 안내](https://openrouter.ai/deepseek/deepseek-v4-flash-0731)

OpenRouter 클라이언트는 기존 OpenAI SDK를 재사용하여 `https://openrouter.ai/api/v1/chat/completions`에 `OPENROUTER_API_KEY`로 Bearer 인증합니다. `LLM_API_BASE`와 OpenAI 키·모델 설정 대신 OpenRouter 설정을 사용합니다. temperature는 0, 제한 시간은 60초, SDK 재시도는 최대 2회입니다. 기존 thinking 비활성화 동작은 `reasoning.enabled=false`로 전달합니다. 구조화 응답은 기존 Pydantic 모델과 JSON Schema 검증을 사용하며 `provider.require_parameters=true`로 해당 파라미터를 지원하는 공급자로 라우팅합니다. [SDK 연동](https://openrouter.ai/docs/quickstart), [Reasoning 설정](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens), [구조화 응답](https://openrouter.ai/docs/guides/features/structured-outputs)

Triplet 생성은 `dataset_path`의 전체 카탈로그에서 관계를 생성하고 테이블명을 검증한 뒤 원본 설명을 복원하고 중복을 제거합니다. `<RDF_TRIPLET_OPEN_ROUTER>/triplets.json`을 원자적으로 전체 교체하며 실패 시 기존 파일을 유지합니다. 상대 경로는 프로젝트 루트 기준입니다. 이 경로를 일반 `RDF_TRIPLET` 또는 OpenAI `RDF_TRIPLET_OPEN_AI`와 같게 설정하면 생성·관계 검색 요청을 500으로 거부합니다.

SQL 생성과 스키마 검색은 현재 `query`와 최근 `history[-3:]`을 반영합니다. SQL 생성은 제공한 `schema`로 SQL 문자열을 반환하고, LLM 스키마 검색은 전체 카탈로그에서 한 번의 호출로 필요한 테이블을 선택합니다. 두 기능은 RDF 파일을 요구하지 않습니다. SQL 생성 요청·응답은 기존 `LlmBasedGenerateSqlRequest`·`LlmBasedGenerateSqlResponse`, triplet 생성 응답은 `GenerateTripletResponse`를 재사용합니다. LLM 스키마 검색과 only-triplet 검색에는 각각 `LlmBasedSchemaOpenRouterRequest`·`LlmBasedSchemaOpenRouterResponse`, `OnlyTripletBasedSchemaOpenRouterRequest`·`OnlyTripletBasedSchemaOpenRouterResponse`를 사용합니다.

Only-triplet 검색은 OpenRouter를 한 번 호출해 시작점을 선택하고 `<RDF_TRIPLET_OPEN_ROUTER>/triplets.json` 관계를 양방향 탐색합니다. 기본 `top_k=-1`은 시작점 무제한이고 양수는 최대 k개입니다. 기본 `max_hop=3`, `0`은 시작점만, `-1`은 재방문 테이블을 건너뛰며 최대 10-hop까지 탐색하고 새 테이블이 없으면 종료합니다. 최종 재선별 없이 도달한 모든 원본 스키마를 카탈로그 순서대로 반환하며 최종 반환 수에는 `top_k`를 적용하지 않습니다. 임베딩 인덱싱은 필요하지 않습니다. 검색 응답 `data`는 동일 길이의 `table_name`, `schema`, `description` 리스트입니다.

`/retrieve/triplet_based_schema_open_router`는 기존 `/retrieve/triplet_based_schema`처럼 시작점 선택과 최종 재선별에 OpenRouter를 두 번 호출합니다. 동일한 `query`, `history`와 `top_k=-1`, `max_hop=3` 기본값을 사용하며 두 단계 모두 최근 `history[-3:]`을 반영합니다. 시작점을 선택한 후 OpenRouter용 RDF를 확장하고, 후보의 원본 DDL·설명·관계를 두 번째 호출에 전달하여 최종 테이블을 선택합니다. 최종 이름이 확장 후보 안에 있는지 검증하고 선택 순서대로 원본 스키마를 반환합니다. 최종 반환 수는 확장 수보다 적거나 시작점의 `top_k`보다 많을 수 있습니다.

전용 모델은 `TripletBasedSchemaOpenRouterRequest`, `TripletBasedSchemaOpenRouterData`, `TripletBasedSchemaOpenRouterResponse`입니다. 메타데이터에는 `model_name`, 두 호출에서 사용한 `question_token`·`answer_token` 합계, `seed_count`, `expanded_count`, `retrieved_count`, 적용한 hop 상한 `graph_depth`를 기록합니다. 빈 카탈로그는 호출 없이 빈 결과를 반환하고, 시작점이 없으면 한 번 호출한 뒤 종료하며, 최종 선택이 비어 있으면 두 번 호출한 뒤 빈 결과를 반환합니다.

```bash
curl -X POST http://localhost:31065/retrieve/generate_triplet_open_router

curl -X POST http://localhost:31065/retrieve/llm_based_schema_open_router \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/llm_based_schema.json

curl -X POST http://localhost:31065/retrieve/only_triplet_based_schema_open_router \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/triplet_based_schema.json

curl -X POST http://localhost:31065/retrieve/triplet_based_schema_open_router \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/triplet_based_schema.json

curl -X POST http://localhost:31065/generate_sql/llm_based_generate_sql_open_router \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/llm_based_generate_sql.json
```

키·모델 검증은 해당 엔드포인트 요청 시 수행하므로 OpenRouter 키 없이도 서버 시작과 기존 API 사용이 가능합니다. 설정·카탈로그·저장 오류는 500, OpenRouter 통신·인증·요청 한도·응답 검증 오류는 502입니다. Triplet·only-triplet 검색은 RDF 누락 시 404와 `/retrieve/generate_triplet_open_router` 안내를 반환하고, 잘못된 RDF는 409입니다. 입력 검증은 기존 422 응답을 사용합니다. API 키는 설정 객체의 문자열 표현에서 제외하고 오류 응답·로그에서 `[REDACTED]`로 마스킹합니다. 메타데이터의 `model_name`에는 실제 모델 ID를, 토큰 필드에는 공급자가 반환한 사용량을 기록합니다.

```bash
python3 -m pytest tests/test_open_router_endpoints.py -q
python3 -m pytest tests/test_triplet_based_schema_open_router.py -q
```

테스트는 모의 HTTP로 OpenRouter 인증·모델·구조화 응답, 대화와 원본 스키마 반영, RDF 저장소 분리와 실패 시 보존, hop 경계·키 마스킹·취소 처리를 확인합니다.

## POST /retrieve/indexing_schema

요청 본문 없이 호출합니다.

```bash
curl -X POST http://localhost:31065/retrieve/indexing_schema
```

입력은 다음 형식의 JSON 배열입니다.

```json
[
  {
    "table_name": "EXAMPLE",
    "schema": "CREATE TABLE EXAMPLE (id INTEGER);",
    "description": "예제 데이터를 저장하는 테이블입니다."
  }
]
```

테이블마다 다음 문자열을 임베딩합니다. DDL은 임베딩에 포함하지 않고 원본 데이터에 보존합니다.

```text
table_name: EXAMPLE
description: 예제 데이터를 저장하는 테이블입니다.
```

1. JSON 전체의 필수 문자열과 중복 테이블 이름을 검사합니다.
2. 저장 디렉터리를 잠그고 기존 인덱스의 메타데이터·원본 일치 여부와 모델을 확인합니다.
3. 저장된 `table_name`에 없는 항목만 임베딩하고 정규화한 벡터를 추가합니다.
4. 임시 디렉터리에 인덱스·메타데이터·원본을 모두 저장한 뒤 교체합니다. 저장 도중 예외가 발생하면 기존 스냅샷을 유지하거나 복구합니다.

`table_name`은 대소문자를 구분합니다. 같은 이름의 설명·DDL이 변경되어도 건너뛰며, 입력에서 제거된 기존 테이블도 유지합니다. 추가할 항목이 없으면 임베딩 서버를 호출하거나 스냅샷을 다시 쓰지 않습니다. 빈 배열도 성공 처리합니다.

저장 구조:

```text
embedded_schema/
└── db_schema/
    ├── index.faiss
    ├── metadata.json
    └── raw_data.json
```

`index.faiss`는 정규화된 벡터의 `IndexFlatIP` 인덱스입니다. `metadata.json`에는 임베딩 모델, 차원, 코사인 메트릭과 벡터 위치별 `table_name`이 들어갑니다. `raw_data.json`에는 저장 당시의 원본 항목을 벡터 순서대로 보존합니다. 두 JSON 파일은 UTF-8로 저장합니다.

성공 응답 예시:

```json
{
  "data": {
    "added_count": 1,
    "skipped_count": 0,
    "total_count": 1,
    "added_tables": ["EXAMPLE"]
  },
  "meta_data": [
    {
      "embedding_model": "BAAI/bge-m3",
      "dimension": 1024,
      "index_directory": "/project/text_to_sql_api/embedded_schema/db_schema"
    }
  ],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.5
}
```

차원, 경로, 시간은 실제 실행 결과에 따라 달라집니다. `added_count`는 이번 호출에서 추가한 수, `skipped_count`는 입력 중 이미 저장되어 건너뛴 수, `total_count`는 저장된 전체 테이블 수입니다. 제공된 97개 테이블의 경우 최초 호출은 추가 97개, 같은 입력의 재호출은 추가 0개·건너뜀 97개가 됩니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 인덱싱 성공 또는 추가 대상 없음 |
| 409 | 기존 인덱스 손상, 파일 불일치, 모델 또는 벡터 차원 불일치 |
| 500 | 입력 파일·JSON·설정·저장 오류 |
| 502 | 임베딩 서버 호출 실패 또는 잘못된 임베딩 벡터 |

오류 응답도 같은 공통 필드를 사용하며 `status="error"`, `data=null`, `msg`에 원인을 반환합니다. 인덱싱 API는 신규 테이블 추가를 지원하며 기존 테이블을 재임베딩하지 않습니다.

## POST /retrieve/indexing_triplet

요청 본문 없이 호출하면 `<RDF_TRIPLET>/triplets.json`을 읽고 아직 인덱싱되지 않은 관계만 임베딩하여 저장합니다. 입력 파일명은 기존 triplet 생성 API와 같은 **`triplets.json`**입니다. 기본 파일이 없을 때만 `.env`의 `<RDF_TRIPLET_OPEN_ROUTER>/triplets.json`을 대신 읽습니다. 두 경로 모두 파일이 없으면 `/retrieve/generate_triplet` 또는 `/retrieve/generate_triplet_open_router`로 생성하거나 같은 RDF 배열 형식으로 준비합니다. 별도의 원본 테이블 카탈로그나 LLM·OpenAI·OpenRouter API 키는 인덱싱에 필요하지 않습니다.

기본 파일이 `[]`이면 그대로 0건으로 처리합니다. 잘못된 JSON·RDF 형식, 권한·읽기 오류에는 대체 경로를 사용하지 않고 500을 반환합니다. 기본 파일이 없어 선택한 대체 파일도 없거나 유효하지 않으면 500을 반환하고 기존 인덱스를 보존합니다. 대체 경로는 입력에만 적용하며, 저장 위치와 임베딩 모델은 기존 설정을 유지합니다.

```bash
curl -X POST http://localhost:31065/retrieve/indexing_triplet
```

임베딩은 `.env`의 `EMBEDDING_API_BASE`와 기존 `embedding_model_name` 설정을 사용합니다. 기본 모델은 `BAAI/bge-m3`이며 `--embedding_model_name`으로 변경할 수 있습니다. OpenAI 호환 클라이언트는 `dummy` 인증을 사용하고, 신규 관계의 문자열을 임베딩 서버에 전달합니다. `OPENAI_API_KEY`는 사용하지 않습니다.

입력 triplet의 각 항목은 `[주체 객체, 관계 문자열, 대상 객체]`이며 객체는 `table_name`, `description`을 포함합니다. 전체 입력의 형식을 검증한 뒤 **주체 테이블명·관계 문자열·대상 테이블명**을 키로 신규 여부를 판단합니다. 이 키의 JSON 배열을 `ensure_ascii=False`, 공백 없는 구분자로 직렬화하여 UTF-8 SHA-256의 소문자 64자리 ID를 만듭니다. 이름과 관계는 대소문자를 구분하며 관계 표현의 양끝 공백은 기존 RDF 검증 규칙에 따라 정리합니다.

같은 키의 설명만 바뀌면 기존 설명·벡터를 유지하고 건너뜁니다. 입력에서 빠진 기존 관계도 유지합니다. 입력 내 같은 키가 반복되면 첫 항목만 사용합니다. 방향이나 관계 표현이 다른 항목은 별도 triplet입니다.

각 신규 triplet에는 다음 필드 순서로 텍스트를 구성하며 주체·대상의 원본 설명을 포함합니다.

```text
subject_table_name: ROOM
subject_description: 공간 정보
predicate: belongs to
object_table_name: BUILDING
object_description: 건물 정보
```

반환 벡터의 수·차원·유한값·영벡터 여부를 검사하고 정규화하여 FAISS `IndexFlatIP`에 추가합니다. 모델·차원이 저장된 인덱스와 다르면 409로 거부합니다. 기존 스키마 인덱스와 독립된 저장 구조는 다음과 같습니다.

```text
<EMBEDDED_TRIPLET>/
└── triplets/
    ├── index.faiss
    ├── metadata.json
    └── raw_data.json
```

`metadata.json`에는 임베딩 모델·차원·코사인 메트릭과 각 행의 `index`, `triplet_id`를 저장합니다. `raw_data.json`에는 같은 순서로 `triplet_id`와 검증된 원본 `triplet`을 보관합니다. 같은 디렉터리의 세 파일은 하나의 스냅샷입니다.

기존 인덱스를 불러올 때 모델·차원·건수·행 순서·ID와 원본 관계의 일치를 확인합니다. 저장소 잠금 안에서 로드·신규 관계 선별·임베딩·저장을 수행하므로 동시 요청이 같은 관계를 중복 임베딩하지 않습니다. 새 파일을 임시 디렉터리에 쓴 뒤 교체하며 저장 실패 시 이전 스냅샷을 보존하거나 복구합니다. 요청이 취소되면 진행 중인 인덱싱 작업이 정리될 때까지 기다립니다.

성공 응답 예시의 ID·차원·시간은 설명용입니다.

```json
{
  "data": {
    "added_count": 1,
    "skipped_count": 2,
    "total_count": 3,
    "added_triplet_ids": ["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]
  },
  "meta_data": [{
    "embedding_model": "BAAI/bge-m3",
    "dimension": 1024,
    "index_directory": "/project/text_to_sql_api/embedded_triplet/triplets"
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.5
}
```

전용 응답 모델은 `IndexingTripletData`, `IndexingTripletResponse`입니다. `added_count`는 이번에 추가한 고유 관계 수, `skipped_count`는 입력 중 기존 관계나 중복으로 건너뛴 항목 수, `total_count`는 누적 저장된 관계 수입니다. `added_triplet_ids`는 이번 추가 순서의 ID 목록입니다.

새로운 관계가 없으면 임베딩 서버를 호출하거나 스냅샷을 다시 쓰지 않습니다. 빈 입력도 성공이며 최초 빈 입력에서는 스냅샷을 생성하지 않고 `dimension: null`을 반환합니다. 입력 RDF 파일은 수정하지 않습니다. 이 API는 인덱싱만 수행하며 기존 스키마 검색 API의 검색 방식을 변경하지 않습니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 신규 관계 추가 완료 또는 추가할 관계 없음 |
| 409 | 기존 인덱스 손상·파일 불일치·중복 또는 잘못된 ID·모델·차원 불일치 |
| 500 | 기본·대체 입력 파일 모두 누락, 선택한 입력의 JSON·RDF 형식·읽기 오류, 설정·저장 오류 |
| 502 | 임베딩 호출 실패 또는 잘못된 벡터 |

오류는 기존 공통 형식인 `data: null`, `meta_data: []`, `status: "error"`, `msg`, `elapsed_time`을 사용합니다. 로그는 `[indexing_triplet]`과 요청별 `request_id`로 구분하고 실제 입력 경로·대체 사유와 로드·잠금·신규 선별·임베딩·저장 단계의 건수와 시간을 기록합니다.

```bash
python3 -m pytest tests/test_indexing_triplet.py -q
```

테스트는 임시 RDF 파일·저장소와 모의 임베딩·HTTP 전송을 사용하여 기본 입력 우선·누락 시 대체 경로 사용, 빈 입력·오류 처리, 증분 추가·중복·설명 변경·삭제 관계 유지, 원본·기존 벡터 보존, 저장 복구·동시 요청·취소와 환경변수 우선순위를 검증합니다. 실제 임베딩 서버는 호출하지 않습니다.

## POST /retrieve/indexing_triplet_open_ai

요청 본문 없이 호출하면 `.env`의 `RDF_TRIPLET_OPEN_AI` 경로 아래 `triplets.json`을 읽고 아직 저장되지 않은 관계만 OpenAI로 임베딩합니다. 기본 입력은 프로젝트 루트 기준 `rdf_triplet_open_ai/triplets.json`입니다. 입력이 없으면 `/retrieve/generate_triplet_open_ai`로 생성하거나 같은 RDF 배열 형식으로 준비합니다. 입력 파일이 없거나 잘못되면 500을 반환하며 `<RDF_TRIPLET>/triplets.json`으로 대체하지 않습니다.

```bash
curl -X POST http://localhost:31065/retrieve/indexing_triplet_open_ai
```

설정은 `.env`의 `RDF_TRIPLET_OPEN_AI`, `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL_NAME`, `EMBEDDED_TRIPLET_OPEN_AI`를 사용합니다. 기본 모델은 기존 `text-embedding-3-small`입니다. 기존 OpenAI 임베딩 클라이언트로 공식 주소 `https://api.openai.com/v1/embeddings`를 키 인증하여 호출하며 주체·대상 이름과 원본 설명, 관계를 텍스트로 전달합니다. 호출 제한 시간은 60초, SDK 재시도는 최대 2회입니다. `EMBEDDING_API_BASE` 설정은 필요하지 않습니다.

주체 테이블명·관계·대상 테이블명의 SHA-256 ID, 신규 판별, 설명 변경 건너뛰기와 삭제 관계 유지 규칙은 `/retrieve/indexing_triplet`과 같습니다. 입력 중 중복은 최초 항목을 사용합니다. 신규 관계가 없으면 외부 호출·스냅샷 저장을 생략하며 빈 입력도 성공입니다.

저장 경로의 기본값은 다음과 같으며 상대 경로는 프로젝트 루트 기준입니다.

```text
<EMBEDDED_TRIPLET_OPEN_AI>/
└── triplets/
    ├── index.faiss
    ├── metadata.json
    └── raw_data.json
```

키·모델·저장 경로는 RDF 파일 읽기 전에 검증합니다. 빈 입력이나 기존 관계만 있는 요청도 OpenAI 키가 필요합니다. `EMBEDDED_TRIPLET_OPEN_AI`가 `EMBEDDED_TRIPLET`과 같거나 같은 실제 경로를 가리키면 500으로 거부합니다. 두 인덱스는 모델·벡터·메타데이터를 각각 관리하며 원본 RDF 파일은 수정하지 않습니다.

정규화된 벡터를 FAISS `IndexFlatIP`에 추가하고 원본 triplet·메타데이터와 함께 저장합니다. 기존 잠금·검증·교체·복구 방식을 공유하여 동시 요청의 중복을 방지하고 실패 시 이전 스냅샷을 보존합니다. 취소 시 진행 중인 작업의 정리를 기다립니다.

응답 모델은 기존 `IndexingTripletResponse`이며 `data`는 `added_count`, `skipped_count`, `total_count`, `added_triplet_ids`입니다. `meta_data`에는 실제 임베딩 모델·차원·OpenAI 전용 저장 경로를 반환합니다. 다음 ID·차원·시간은 형식 설명용입니다.

```json
{
  "data": {
    "added_count": 1,
    "skipped_count": 2,
    "total_count": 3,
    "added_triplet_ids": ["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]
  },
  "meta_data": [{
    "embedding_model": "text-embedding-3-small",
    "dimension": 1536,
    "index_directory": "/project/text_to_sql_api/embedded_triplet_open_ai/triplets"
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.5
}
```

기존 인덱스 손상·모델·차원 불일치는 409, 키·모델·경로·입력·저장 오류는 500, OpenAI 인증·한도·호출 실패나 잘못된 벡터는 502입니다. 오류는 `data: null`, `meta_data: []`, `status: "error"`, `msg`, `elapsed_time`의 공통 형식으로 반환합니다. API 키는 오류 응답과 로그·예외 추적에서 마스킹하며 로그 작업명은 `[indexing_triplet_open_ai]`입니다.

```bash
python3 -m pytest tests/test_indexing_triplet_open_ai.py -q
```

테스트는 OpenAI 전용 RDF 입력·로컬 입력과 저장소의 독립성·증분 추가·중복·변경 설명 유지, 설정 사전 검증·공식 URL·키 인증·모델·키 마스킹, 저장 복구·동시 요청·취소와 기존 API 호환성을 모의 임베딩·HTTP 전송으로 검증합니다. 실제 OpenAI 서버는 호출하지 않습니다.

## POST /retrieve/embedding_based_schema

저장된 인덱스에서 자연어 질의와 코사인 유사도가 높은 순서대로 테이블 정보를 반환합니다. 먼저 `/retrieve/indexing_schema`로 인덱스를 생성합니다. 검색은 `.env`의 `EMBEDDED_SCHEMA` 아래 `db_schema/`에 있는 인덱스와 원본을 사용하며 `dataset/db_schema.json`을 다시 읽지 않습니다.

전용 모델은 `EmbeddingBasedSchemaRequest`, `ConversationHistoryMessage`, `EmbeddingBasedSchemaData`, `EmbeddingBasedSchemaResponse`입니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 사용자의 자연어 질의. 빈 문자열·공백뿐인 값은 허용하지 않습니다. |
| `top_k` | 필수 양의 정수 | 반환할 테이블 수. 숫자 문자열·실수·불리언은 허용하지 않습니다. |
| `history` | 선택 메시지 리스트 | 과거 대화. 기본값은 `[]`이며, 오래된 메시지부터 최신 메시지 순서로 전달합니다. |

```bash
curl -X POST http://localhost:31065/retrieve/embedding_based_schema \
  -H 'Content-Type: application/json' \
  -d '{"query":"학기별 과목과 학과 정보를 조회하고 싶어","top_k":2}'
```

대화 히스토리를 함께 전달하는 요청 예시:

```json
{
  "query": "그중 2학기 과목만 조회하고 싶어",
  "top_k": 3,
  "history": [
    {"role": "user", "content": "학과별 과목 정보를 조회하고 싶어"},
    {"role": "assistant", "content": "학과와 과목 정보를 조회하는 테이블을 찾았습니다."}
  ]
}
```

각 메시지의 `role`은 `user` 또는 `assistant`, `content`는 공백뿐인 값을 허용하지 않는 문자열입니다. **메시지 하나를 1 turn으로 계산**하며, 검색에는 `history[-3:]`의 최대 3개 과거 메시지와 현재 `query`를 사용합니다. 사용자·답변 한 쌍은 메시지 2개이며, 현재 질의는 과거 3개 제한에 포함하지 않습니다.

히스토리가 있으면 선택한 메시지의 순서와 역할을 유지하고 현재 질의를 마지막에 붙여 다음 문자열 하나를 임베딩합니다. 메시지 사이에는 빈 줄을 넣습니다.

```text
user: 학과별 과목 정보를 조회하고 싶어

assistant: 학과와 과목 정보를 조회하는 테이블을 찾았습니다.

user: 그중 2학기 과목만 조회하고 싶어
```

히스토리를 생략하거나 `[]`로 보내면 기존처럼 `query` 원문만 임베딩합니다. `history`는 요청마다 전달하며 서버에서 별도로 누적하지 않습니다. 입력된 히스토리의 모든 메시지는 요청 스키마로 검증하고, 임베딩에는 최근 3개만 사용합니다.

검색 문자열은 인덱싱과 같은 `BAAI/bge-m3` 모델, `EMBEDDING_API_BASE` 서버로 한 번 임베딩하고 정규화합니다. 유사도 임계값 없이 상위 `top_k`개를 선택합니다. `top_k`가 저장된 테이블 수보다 크면 존재하는 전체 테이블을 반환합니다.

응답 예시의 테이블·시간은 설명용입니다. `data`는 필드별 리스트를 가진 객체이며, 세 리스트의 같은 위치가 동일한 테이블입니다. 모든 리스트는 검색 유사도 내림차순을 유지합니다. `schema`는 임베딩에 넣었던 문자열이 아닌 인덱싱 당시 저장한 원본 DDL입니다.

```json
{
  "data": {
    "table_name": ["TABLE_A", "TABLE_B"],
    "schema": [
      "CREATE TABLE TABLE_A (id INTEGER);",
      "CREATE TABLE TABLE_B (id INTEGER);"
    ],
    "description": ["첫 번째 테이블 설명", "두 번째 테이블 설명"]
  },
  "meta_data": [
    {"embedding_model": "BAAI/bge-m3", "top_k": 2, "retrieved_count": 2}
  ],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.12
}
```

공유 잠금으로 인덱스·메타데이터·원본을 일관되게 읽은 뒤 잠금을 해제하고 질의를 임베딩·검색합니다. 검색은 저장 파일을 갱신하지 않습니다. 유효한 빈 인덱스는 임베딩을 생략하고 `table_name`, `schema`, `description`에 빈 리스트를 반환합니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 검색 성공. 유효한 빈 인덱스는 빈 결과 반환 |
| 404 | 저장 디렉터리 또는 인덱스가 아직 없음 |
| 409 | 인덱스 손상·원본 불일치·모델 또는 질의 벡터 차원 불일치 |
| 422 | 요청 필드 누락 또는 잘못된 `query`·`top_k`·`history` |
| 500 | 설정·로컬 처리 오류 |
| 502 | 질의 임베딩 호출 실패 또는 유효하지 않은 벡터 |

404·409·500·502는 공통 응답 형식을 사용합니다.

```json
{
  "data": null,
  "meta_data": [],
  "status": "error",
  "msg": "Schema index was not found. Run /retrieve/indexing_schema first.",
  "elapsed_time": 0.01
}
```

422는 잘못된 필드의 위치와 사유를 담은 FastAPI 기본 `detail` 응답을 사용합니다.

## POST /retrieve/embedded_triplet_based_schema

`<EMBEDDED_TRIPLET>/triplets/`의 FAISS 인덱스에서 질의·최근 대화와 유사한 triplet을 검색하고, 관계의 주체·대상 테이블을 중복 제거하여 상위 `top_k`개 테이블의 원본 스키마를 반환합니다. 전용 모델은 `EmbeddedTripletBasedSchemaRequest`, `EmbeddedTripletBasedSchemaData`, `EmbeddedTripletBasedSchemaResponse`입니다.

먼저 `/retrieve/indexing_triplet`으로 인덱스를 준비합니다. 질의 임베딩에는 해당 인덱싱과 같은 `EMBEDDING_API_BASE`, `embedding_model_name` 설정을 사용합니다. 기본 모델은 `BAAI/bge-m3`이며 모델·차원이 저장된 인덱스와 같아야 합니다. 검색에는 저장된 triplet 원본과 기존 `dataset_path`의 테이블 카탈로그를 사용합니다. RDF 원본 파일이나 스키마 임베딩 인덱스, LLM·OpenAI 키는 필요하지 않습니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 현재 자연어 질의. 빈 문자열·공백뿐인 값은 허용하지 않습니다. |
| `history` | 선택 메시지 리스트 | 기존 검색과 같은 `role`·`content` 형식. 기본값은 `[]`이며 `history[-3:]`만 사용합니다. |
| `top_k` | 필수 양의 정수 | 반환할 중복 없는 테이블 수입니다. 숫자 문자열·실수·불리언은 허용하지 않습니다. |

정의되지 않은 요청 필드는 422로 거부합니다. 입력 히스토리 전체를 검증한 뒤 최근 3개 메시지의 역할·순서를 유지해 현재 질의 앞에 결합합니다. 메시지 하나가 1 turn이며 현재 질의는 과거 3개 제한에 포함하지 않습니다. 히스토리를 생략하거나 `[]`로 전달하면 `query` 원문만 임베딩합니다.

```bash
curl -X POST http://localhost:31065/retrieve/embedded_triplet_based_schema \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"그중 사용 중인 공간의 캠퍼스 정보를 조회해줘","top_k":3,"history":[{"role":"user","content":"공간과 건물 정보를 조회하고 싶어"}]}'
```

검색과 순위 계산은 다음 규칙을 사용합니다.

1. 공유 잠금 안에서 triplet 인덱스·메타데이터·원본을 일관되게 읽고 검증한 뒤 잠금을 해제합니다.
2. 현재 원본 카탈로그를 읽고 인덱스에 등장하는 테이블명을 매핑합니다. 원본이 없는 이름은 제외하며 `unmapped_table_count`에 제외된 고유 이름 수를 기록합니다.
3. 질의·최근 대화 문자열을 한 번 임베딩하고 정규화한 뒤 전체 triplet의 코사인 유사도를 조회합니다. Triplet 개수를 먼저 `top_k`로 제한하지 않습니다.
4. 주체·대상 테이블 각각에 대해 **그 테이블이 등장하는 triplet 중 최고 유사도**를 테이블 점수로 사용합니다. 중복·자기 참조는 테이블명으로 합치고 점수 내림차순, 동점은 테이블명 오름차순으로 정렬합니다. 음수 유사도도 제외하지 않습니다.
5. 상위 `top_k`개의 이름을 원본 카탈로그에 매핑하여 DDL·설명을 반환합니다. 인덱스의 과거 설명 대신 현재 원본 값을 사용합니다.

`top_k`는 triplet 수가 아닌 테이블 수입니다. 반환 수는 `min(top_k, 인덱스에 등장하면서 원본 스키마가 있는 고유 테이블 수)`입니다. 인덱스에 없는 카탈로그 테이블을 추가 후보로 사용하지 않습니다. 원본이 없는 이름을 제외한 뒤에도 다른 테이블의 검색은 계속합니다.

응답 예시의 테이블·시간은 설명용입니다. `data`의 세 리스트는 같은 순서·길이이며, 비어 있지 않은 `data`는 SQL 생성 요청의 `schema`에 그대로 전달할 수 있습니다.

```json
{
  "data": {
    "table_name": ["TABLE_A", "TABLE_B"],
    "schema": ["CREATE TABLE TABLE_A (id INTEGER);", "CREATE TABLE TABLE_B (id INTEGER);"],
    "description": ["첫 번째 테이블 원본 설명", "두 번째 테이블 원본 설명"]
  },
  "meta_data": [{
    "embedding_model": "BAAI/bge-m3",
    "top_k": 2,
    "retrieved_count": 2,
    "unmapped_table_count": 1
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.12
}
```

유효한 빈 인덱스 또는 매핑 가능한 테이블이 없는 경우 질의 임베딩을 생략하고 200과 빈 세 리스트를 반환합니다. 아직 스냅샷이 생성되지 않은 경우는 404입니다. 빈 결과는 테이블이 하나 이상 필요한 SQL 생성 API에 전달하지 않습니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 검색 성공. 후보 부족·빈 결과도 포함 |
| 404 | Triplet 인덱스 미생성. `/retrieve/indexing_triplet` 실행 필요 |
| 409 | 인덱스 손상·검색 결과 불일치·모델 또는 질의 벡터 차원 불일치 |
| 422 | 잘못된 질의·히스토리·top_k 또는 추가 요청 필드 |
| 500 | 설정·원본 카탈로그 누락·형식·중복 테이블 또는 로컬 처리 오류 |
| 502 | 임베딩 호출 실패 또는 유효하지 않은 질의 벡터 |

404·409·500·502는 `data: null`, `meta_data: []`, `status: "error"`, `msg`, `elapsed_time`의 공통 오류 응답을 반환하고 422는 FastAPI 기본 `detail` 형식입니다. 검색은 원본 파일·인덱스·메타데이터를 수정하지 않습니다. 네트워크 호출 전에 공유 잠금을 해제하고 메모리에 로드된 스냅샷으로 검색하므로 쓰기와 겹쳐도 한 요청의 triplet 데이터는 일관되게 유지됩니다.

로그는 `[embedded_triplet_based_schema]`와 요청별 `request_id`로 구분하고 로딩·원본 매핑·질의 임베딩·검색·순위 집계의 건수와 시간을 기록합니다.

```bash
python3 -m pytest tests/test_embedded_triplet_based_schema.py -q
```

테스트는 모의 임베딩·HTTP 전송과 임시 인덱스·카탈로그를 사용하여 최고 유사도·테이블 중복·동점·음수 점수, 후보 부족·원본 매핑·대화 전달·오류 응답과 공유 읽기·저장 중 검색·잠금 해제·파일 미변경을 검증합니다. 실제 임베딩 서버는 호출하지 않습니다.

## POST /retrieve/embedded_triplet_based_schema_open_ai

`<EMBEDDED_TRIPLET_OPEN_AI>/triplets/`의 임베딩 인덱스에서 `query`와 `history[-3:]`으로 검색하여 중복 없는 상위 `top_k`개 테이블의 원본 스키마를 반환합니다. 먼저 `/retrieve/indexing_triplet_open_ai`를 호출하여 인덱스를 준비합니다.

질의 임베딩은 `.env`의 `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL_NAME`과 공식 OpenAI 주소 `https://api.openai.com/v1`을 사용합니다. 인덱싱과 같은 모델·차원을 사용해야 합니다. 로컬 임베딩 서버·LLM 설정은 필요하지 않으며 `EMBEDDED_TRIPLET`의 인덱스로 대체 검색하지 않습니다. 두 triplet 인덱스 루트는 해석된 실제 경로가 서로 달라야 합니다.

전용 모델은 `EmbeddedTripletBasedSchemaOpenAiRequest`, `EmbeddedTripletBasedSchemaOpenAiData`, `EmbeddedTripletBasedSchemaOpenAiResponse`입니다. 요청의 `query`는 공백뿐이지 않은 필수 문자열, `top_k`는 필수 양의 정수, `history`는 선택 메시지 리스트이며 기본값은 `[]`입니다. 숫자 문자열·실수·불리언 `top_k`와 정의되지 않은 필드는 422로 거부합니다. 히스토리는 전체를 검증한 후 최근 3개 메시지의 역할·순서를 유지해 현재 질의 앞에 결합합니다. 히스토리가 없으면 질의 원문만 임베딩합니다.

```bash
curl -X POST http://localhost:31065/retrieve/embedded_triplet_based_schema_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"그중 사용 중인 공간의 캠퍼스 정보를 조회해줘","top_k":3,"history":[{"role":"user","content":"공간과 건물 정보를 조회하고 싶어"}]}'
```

순위 계산은 `/retrieve/embedded_triplet_based_schema`와 같습니다. 모든 triplet을 검색하고 주체·대상 테이블별 **최고 코사인 유사도**를 집계합니다. 점수 내림차순, 동점은 테이블명 오름차순으로 정렬하며 음수 점수도 유지합니다. 원본 카탈로그에 없는 이름은 제외하고, 반환 개수는 `min(top_k, 원본 매핑이 가능한 고유 테이블 수)`입니다. `dataset_path` 카탈로그의 현재 DDL·설명을 반환합니다.

`data`는 같은 순서·길이의 `table_name`, `schema`, `description` 리스트를 담습니다. 응답 예시의 테이블·시간은 설명용입니다.

```json
{
  "data": {
    "table_name": ["TABLE_A", "TABLE_B"],
    "schema": ["CREATE TABLE TABLE_A (id INTEGER);", "CREATE TABLE TABLE_B (id INTEGER);"],
    "description": ["첫 번째 테이블 원본 설명", "두 번째 테이블 원본 설명"]
  },
  "meta_data": [{
    "embedding_model": "text-embedding-3-small",
    "top_k": 3,
    "retrieved_count": 2,
    "unmapped_table_count": 0
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.12
}
```

유효한 빈 인덱스 또는 매핑 가능한 테이블이 없는 경우 OpenAI 호출을 생략하고 빈 세 리스트를 반환합니다. 이 경우에도 OpenAI 키·모델·저장 경로 설정은 먼저 검증합니다. 원본·인덱스·메타데이터는 수정하지 않고 공유 잠금으로 일관된 스냅샷을 읽은 뒤, 네트워크 호출 전에 잠금을 해제합니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 검색 성공. 후보 부족·빈 결과도 포함 |
| 404 | OpenAI triplet 인덱스 미생성. `/retrieve/indexing_triplet_open_ai` 실행 필요 |
| 409 | 인덱스 손상·모델 또는 질의 벡터 차원 불일치 |
| 422 | 잘못된 질의·히스토리·top_k 또는 추가 요청 필드 |
| 500 | OpenAI 키·모델·경로 설정 또는 원본 카탈로그 오류 |
| 502 | OpenAI 임베딩 호출 실패 또는 유효하지 않은 질의 벡터 |

404·409·500·502는 전용 응답 모델의 `data: null`, `meta_data: []`, `status: "error"`, `msg`, `elapsed_time`으로 반환합니다. 422는 FastAPI 기본 `detail` 형식입니다. 로그의 operation은 `embedded_triplet_based_schema_open_ai`이며 오류 응답과 예외 로그의 API 키는 `[REDACTED]`로 가립니다.

```bash
python3 -m pytest tests/test_embedded_triplet_based_schema_open_ai.py -q
```

테스트는 임시 인덱스와 모의 HTTP 전송으로 검색 순위·대화 전달·인덱스 분리·공식 주소와 인증·오류 처리·동시 읽기·잠금 해제·취소·파일 미변경을 검증합니다. 실제 OpenAI API는 호출하지 않습니다.

## POST /retrieve/llm_based_schema

사용자 질의와 최근 대화에 필요한 테이블을 원본 카탈로그 전체에서 LLM으로 선택합니다. 전용 모델은 `LlmBasedSchemaRequest`, `LlmBasedSchemaData`, `LlmBasedSchemaResponse`입니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 현재 자연어 질의. 빈 문자열·공백뿐인 값은 허용하지 않습니다. |
| `history` | 선택 메시지 리스트 | `role`은 `user` 또는 `assistant`, `content`는 공백뿐이지 않은 문자열입니다. 기본값은 `[]`이며 `history[-3:]`만 LLM에 전달합니다. |

`top_k`, `schema` 등 정의되지 않은 요청 필드는 422로 거부합니다. 메시지 하나가 1 turn이며 현재 질의는 과거 3개 제한에 포함하지 않습니다. 입력 히스토리 전체를 검증한 뒤 최근 3개의 역할·내용·순서를 유지하고 현재 질의를 마지막에 전달합니다. 대화는 요청마다 전달하며 서버에서 누적하지 않습니다.

```bash
curl -X POST http://localhost:31065/retrieve/llm_based_schema \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"그중 사용 중인 공간의 캠퍼스 정보를 조회해줘","history":[{"role":"user","content":"공간과 건물 정보를 조회하고 싶어"}]}'
```

요청마다 기존 `dataset_path` 설정의 카탈로그를 읽고 필수 필드·테이블명 중복을 검증합니다. 기본 파일은 프로젝트 기준 `dataset/db_schema.json`이며 `--dataset_path`로 변경할 수 있습니다. **전체 테이블명·설명·원본 DDL과 질의·최근 대화를 한 번의 LLM 호출에 전달**합니다. 필터·집계·중간 연결에 필요한 테이블도 선택하도록 지시하고, 테이블명과 선정 근거를 구조화 응답으로 받습니다.

LLM은 `.env`의 `LLM_API_BASE`, `LLM_MODEL_NAME`과 기존 클라이언트를 사용합니다. 인증은 `dummy`이며 `OPENAI_API_KEY`는 필요하지 않습니다. 온도는 0이고 DeepSeek thinking은 비활성화합니다. 호출 제한 시간은 60초, SDK 재시도는 최대 2회입니다. RDF 파일·임베딩 서버·FAISS 인덱스는 사용하지 않습니다. 전체 입력이 컨텍스트를 초과하면 임의로 자르거나 분할하지 않고 502로 처리합니다.

선택한 이름이 원본 카탈로그에 존재하는지 검증하고 LLM 선택 순서대로 중복 제거하여 원본 DDL·설명을 반환합니다. LLM이 생성한 DDL은 사용하지 않습니다. `data`의 세 리스트는 같은 길이·순서를 유지하며 비어 있지 않은 `data`를 SQL 생성 요청의 `schema`에 그대로 전달할 수 있습니다.

응답 예시의 테이블·토큰·시간은 형식 설명용입니다.

```json
{
  "data": {
    "table_name": ["TABLE_A", "TABLE_B"],
    "schema": ["CREATE TABLE TABLE_A (id INTEGER);", "CREATE TABLE TABLE_B (id INTEGER);"],
    "description": ["첫 번째 테이블 설명", "두 번째 테이블 설명"]
  },
  "meta_data": [{
    "model_name": "DeepSeek-V4-Flash-0731",
    "question_token": 32000,
    "answer_token": 100,
    "table_count": 97,
    "retrieved_count": 2
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 1.2
}
```

토큰 사용량이 없으면 0으로 계산합니다. 카탈로그가 비어 있으면 LLM 호출을 생략하며, 빈 카탈로그나 빈 선택 결과에는 200과 세 개의 빈 리스트를 반환합니다. 설정은 빈 카탈로그에서도 먼저 검증합니다. 빈 검색 결과는 테이블이 하나 이상 필요한 SQL 생성 API로 전달하지 않습니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 검색 성공. 빈 결과도 포함 |
| 422 | 잘못된 질의·히스토리 또는 추가 요청 필드 |
| 500 | LLM·경로 설정, 원본 카탈로그 누락·손상·스키마·중복 테이블 또는 로컬 처리 오류 |
| 502 | LLM 호출 실패·타임아웃·거절·중단·응답 형식 오류 또는 원본에 없는 테이블명 |

500·502는 `data: null`, `meta_data: []`, `status: "error"`, 원인을 담은 `msg`, `elapsed_time`을 반환하며 부분 결과는 반환하지 않습니다. 422는 FastAPI 기본 `detail` 형식입니다. 검색 중 파일은 수정하지 않습니다. 로그는 `[llm_based_schema]`와 요청별 `request_id`로 구분하고 카탈로그 로딩·LLM 호출·선택 검증·완료 단계의 건수·소요 시간을 기록합니다.

```bash
python3 -m pytest tests/test_llm_based_schema.py -q
```

테스트는 모의 LLM과 HTTP 전송을 사용하여 전체 DDL·최근 대화 전달, 단일 호출, 원본 매핑·순서·중복 제거, 빈 결과, 입력·설정·카탈로그·LLM 오류, 취소 및 클라이언트 종료를 검증합니다. 실제 LLM 서버는 호출하지 않습니다.

## POST /retrieve/llm_based_schema_open_ai

기존 LLM 기반 검색과 동일하게 전체 테이블명·설명·원본 DDL과 `query`, 최근 `history[-3:]`을 한 번의 LLM 호출에 전달하여 필요한 테이블을 선택합니다. 전용 모델은 `LlmBasedSchemaOpenAiRequest`, `LlmBasedSchemaOpenAiData`, `LlmBasedSchemaOpenAiResponse`입니다.

요청은 필수 `query`와 선택 `history`만 받으며 대화 형식·검증은 기존 LLM 검색과 같습니다. `top_k`, `schema` 등 추가 필드는 422로 거부합니다. 카탈로그는 기존 `dataset_path`를 사용하고, 모델은 `.env`의 `OPENAI_LLM_MODEL_NAME`, 인증은 `OPENAI_API_KEY`를 사용합니다. 서버는 공식 주소 `https://api.openai.com/v1`를 호출하며 `LLM_API_BASE`와 RDF·임베딩 설정은 필요하지 않습니다.

```bash
curl -X POST http://localhost:31065/retrieve/llm_based_schema_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary @tests/test_doc/llm_based_schema.json
```

기본 모델 `gpt-6-luna`에는 기존 OpenAI 호출 옵션인 `reasoning_effort="none"`을 적용합니다. 호출 제한 시간은 60초, SDK 재시도는 최대 2회입니다. 입력 카탈로그를 임의로 자르거나 분할하지 않으며, 컨텍스트 초과는 502로 반환합니다.

LLM이 선택한 이름을 원본 카탈로그와 검증하고 선택 순서대로 중복 제거하여 DDL·설명을 복원합니다. `data`는 같은 순서·길이의 세 문자열 리스트이며 SQL 생성 요청의 `schema`에 그대로 전달할 수 있습니다. 다음 수치와 테이블은 형식 설명용 예시입니다.

```json
{
  "data": {
    "table_name": ["TABLE_A"],
    "schema": ["CREATE TABLE TABLE_A (id INTEGER);"],
    "description": ["테이블 원본 설명"]
  },
  "meta_data": [{
    "model_name": "gpt-6-luna",
    "question_token": 32000,
    "answer_token": 50,
    "table_count": 97,
    "retrieved_count": 1
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 1.2
}
```

키·모델은 카탈로그를 읽기 전에 검증합니다. 빈 카탈로그에서는 LLM 호출을 생략하고, 빈 카탈로그나 빈 선택 결과는 200과 세 개의 빈 리스트로 반환합니다. 설정·카탈로그 오류는 500, OpenAI 인증·한도·호출 실패나 유효하지 않은 선택 응답은 502로 처리합니다. 실패 시 부분 결과를 반환하지 않으며 `data: null`, `meta_data: []`, `status: "error"`, `msg`, `elapsed_time`의 공통 오류 응답을 사용합니다. 키는 오류 응답·로그·예외 추적에서 마스킹합니다.

원본 파일은 수정하지 않습니다. 로그 작업명은 `[llm_based_schema_open_ai]`이며 요청별 식별자, 카탈로그 수·반환 수와 단계별 처리 시간을 기록합니다.

```bash
python3 -m pytest tests/test_llm_based_schema_open_ai.py -q
```

테스트는 모의 키·LLM·HTTP 전송을 사용하여 공식 주소·인증·모델 옵션, 전체 DDL·대화 전달, 전용 모델, 오류·키 마스킹·취소와 기존 검색 호환성을 검증하며 실제 OpenAI 서버를 호출하지 않습니다.

## POST /retrieve/triplet_based_schema

`retrieval_rdf.md`의 추천 흐름에 따라 사용자 질의와 대화로 필요한 테이블을 선택하고 원본 스키마를 반환합니다. 전용 요청·응답 모델은 `TripletBasedSchemaRequest`, `TripletBasedSchemaData`, `TripletBasedSchemaResponse`입니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 현재 질의. 빈 문자열·공백뿐인 값은 허용하지 않습니다. |
| `history` | 선택 메시지 리스트 | 기존 검색과 같은 `role`·`content` 형식. 기본값은 `[]`이며 최근 `history[-3:]`만 사용합니다. |
| `max_hop` | 선택 정수 | 기본값 `3`. `0`은 시작 테이블만, 양수는 해당 깊이까지, `-1`은 최대 10-hop까지 탐색합니다. |
| `top_k` | 선택 정수 | 시작 테이블 수의 상한. 기본값 `-1`은 무제한이고 양수는 최대 k개입니다. 최종 반환 수에는 적용하지 않습니다. |

정의되지 않은 요청 필드는 422로 거부합니다. `max_hop`은 `-1` 또는 0 이상의 정수, `top_k`는 `-1` 또는 양의 정수여야 하며 문자열·실수·불리언·`null`은 허용하지 않습니다. 메시지 하나가 1 turn이고 현재 질의는 과거 3개 제한에 포함하지 않습니다. 히스토리는 요청마다 전달하며 서버에서 누적하지 않습니다.

원본 카탈로그는 `dataset/db_schema.json`이며 `--dataset_path`로 변경할 수 있습니다. 관계는 `.env`의 `<RDF_TRIPLET>/triplets.json`에서 읽습니다. 이 파일이 없으면 먼저 `POST /retrieve/generate_triplet`을 호출합니다. 임베딩 서버나 FAISS 인덱스는 필요하지 않습니다. `RDF_TRIPLET_OPEN_AI` 파일을 자동으로 대신 사용하거나 검색 중 triplet을 생성하지 않습니다.

검색 단계는 다음과 같습니다.

1. 전체 원본 테이블명·설명과 질의·최근 대화를 LLM에 전달해 시작 후보와 선정 근거를 받습니다. `top_k`가 양수이면 관련도 순서대로 최대 k개를 선택하도록 지시하고, 이름 검증·중복 제거 후 초과한 시작점은 LLM 응답 순서대로 제한합니다. 관련 후보가 부족하면 적게 선택할 수 있습니다. RDF에 등장하지 않는 독립 테이블도 선택 대상입니다.
2. 관계를 중복 제거하고 시작 후보의 들어오는 관계와 나가는 관계를 모두 요청의 `max_hop`에 따라 탐색합니다(기본 3단계). `-1`은 최대 10단계이며 순환·자기 참조 등으로 재방문한 테이블은 건너뛰고 미방문 테이블은 계속 탐색합니다. 새로운 테이블이 없으면 종료합니다.
3. 확장된 후보의 원본 DDL·설명과 원래 방향을 유지한 관계, 동일한 질의·최근 대화를 LLM에 제공해 최종 재선별합니다. 필터·집계·중간 연결에 필요한 테이블을 유지하도록 지시합니다. 추론된 관계를 확정된 외래 키나 조인 조건으로 취급하지 않습니다.
4. 최종 이름이 후보 안에 존재하는지 검증하고, LLM 선택 순서대로 중복 제거하여 원본 카탈로그의 DDL·설명을 반환합니다. LLM이 DDL을 새로 작성하지 않습니다.

두 LLM 호출은 `LLM_API_BASE`, `LLM_MODEL_NAME`과 기존 LLM 클라이언트를 사용합니다. 인증에는 `dummy`를 사용하며 `OPENAI_API_KEY`를 사용하지 않습니다. 온도는 0이고 DeepSeek thinking은 비활성화합니다. 요청별 시간 제한은 60초, SDK 재시도는 최대 2회입니다. 확장 후보 수 상한은 없으며, 컨텍스트 초과는 후보를 임의로 자르지 않고 502로 반환합니다. 최종 재선별에는 시작점의 `top_k` 제한을 적용하지 않습니다.

```bash
curl -X POST http://localhost:31065/retrieve/triplet_based_schema \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"그중 2학기 과목만 조회해줘","history":[{"role":"user","content":"학과별 과목 정보를 조회하고 싶어"}],"max_hop":3,"top_k":1}'
```

응답 예시의 테이블·토큰·시간은 형식 설명용입니다. 세 리스트의 같은 위치는 같은 테이블입니다.

```json
{
  "data": {
    "table_name": ["TABLE_A", "TABLE_B"],
    "schema": ["CREATE TABLE TABLE_A (id INTEGER);", "CREATE TABLE TABLE_B (id INTEGER);"],
    "description": ["첫 번째 테이블 설명", "두 번째 테이블 설명"]
  },
  "meta_data": [{
    "model_name": "DeepSeek-V4-Flash-0731",
    "question_token": 1200,
    "answer_token": 150,
    "seed_count": 1,
    "expanded_count": 4,
    "retrieved_count": 2,
    "graph_depth": 3
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 1.2
}
```

토큰 수는 실제 수행한 두 단계의 합계이며 사용량이 없는 응답은 0으로 계산합니다. `expanded_count`는 시작 후보를 포함한 전체 확장 후보 수입니다. `graph_depth`는 적용된 최대 탐색 깊이이며 `max_hop=-1`이면 `10`입니다. 조기 종료 여부와 관계없이 실제 탐색 완료 깊이가 아닌 상한을 표시합니다. 시작 후보가 없으면 두 번째 LLM 호출을 생략하고, 빈 카탈로그에서는 모든 LLM 호출을 생략합니다. 후보나 최종 선택이 없으면 200과 세 개의 빈 리스트를 반환합니다. 비어 있지 않은 응답의 `data`는 SQL 생성 요청의 `schema`에 그대로 전달할 수 있습니다. 빈 결과일 때는 SQL 생성 API를 호출하지 않고 필요한 테이블을 찾지 못했음을 표시합니다.

요청마다 최신 카탈로그와 RDF 파일을 읽으며 원본 파일은 수정하지 않습니다. 로그는 `[triplet_based_schema]`와 요청별 `request_id`로 구분하고 로딩, 시작 후보 선택, 관계 확장, 재선별과 완료 단계의 건수·시간을 출력합니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 검색 성공. 관련 테이블이 없는 빈 결과도 포함 |
| 404 | RDF 파일 미생성. `/retrieve/generate_triplet` 실행 필요 |
| 409 | RDF 형식 손상 또는 원본 카탈로그에 없는 테이블 참조. RDF 재생성 필요 |
| 422 | 잘못된 질의·히스토리·탐색 깊이·시작 테이블 수 또는 추가 요청 필드 |
| 500 | LLM·경로 설정 또는 원본 카탈로그·로컬 처리 오류 |
| 502 | LLM 호출 실패·거절·중단, 잘못된 구조화 응답 또는 허용 후보 밖의 테이블명 |

404·409·500·502는 기존과 동일하게 `data: null`, `meta_data: []`, `status: "error"`, 오류 원인의 `msg`, `elapsed_time`을 반환합니다. 어느 단계든 실패하면 부분 결과를 반환하지 않습니다. 422는 FastAPI 기본 `detail` 형식입니다.

```bash
python3 -m pytest tests/test_triplet_based_schema.py -q
```

테스트는 모의 LLM과 HTTP 전송을 사용하여 요청별 깊이의 양방향 탐색, 중간 연결·독립 테이블, 원본 매핑·대화 문맥, 입력 검증, 오류·취소, 실제 클라이언트의 설정 URL 및 인증을 확인하며 실서버를 호출하지 않습니다. `tests/test_triplet_max_hop.py`에서 요청 기본값·허용 범위, 10-hop 경계, 순환·자기 참조와 여러 시작 테이블의 탐색을 확인합니다.

## POST /retrieve/only_triplet_based_schema

시작 테이블 선정에 LLM을 한 번 사용하고, triplet 관계로 확장한 전체 후보의 원본 스키마를 반환합니다. 최종 LLM 재선별은 생략합니다. 전용 모델은 `OnlyTripletBasedSchemaRequest`, `OnlyTripletBasedSchemaData`, `OnlyTripletBasedSchemaResponse`입니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 현재 자연어 질의. 공백뿐인 값은 허용하지 않습니다. |
| `history` | 선택 메시지 리스트 | `role`·`content` 형식이며 기본값은 `[]`입니다. 시작 테이블 선정에 `history[-3:]`만 사용합니다. |
| `max_hop` | 선택 정수 | 기본값 `3`. `0`은 시작 테이블만, 양수는 해당 깊이까지, `-1`은 최대 10-hop까지 탐색합니다. |
| `top_k` | 선택 정수 | 시작 테이블 수의 상한. 기본값 `-1`은 무제한이고 양수는 최대 k개입니다. 확장 후 반환 수에는 적용하지 않습니다. |

기존 triplet 검색과 같은 검증 규칙을 사용합니다. 정의되지 않은 필드는 422로 거부하고, `max_hop`은 `-1` 또는 0 이상의 정수, `top_k`는 `-1` 또는 양의 정수여야 합니다. 문자열·실수·불리언·`null`은 허용하지 않습니다. 시작 테이블의 이름을 검증하고 중복을 제거한 뒤 LLM 응답 순서대로 `top_k`를 적용합니다. 관련 후보가 부족하면 적게 선택할 수 있습니다.

```bash
curl -X POST http://localhost:31065/retrieve/only_triplet_based_schema \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"학과별 과목 정보를 조회해줘","history":[],"max_hop":3,"top_k":1}'
```

카탈로그는 `dataset_path` 설정의 원본 파일을 읽고, 관계는 `<RDF_TRIPLET>/triplets.json`에서 읽습니다. 파일이 없으면 먼저 `/retrieve/generate_triplet`을 실행합니다. 임베딩 인덱스는 필요하지 않습니다.

1. 전체 테이블명·설명과 현재 질의·최근 대화를 `LLM_API_BASE`의 LLM에 전달하여 시작 테이블을 선정합니다. 기존 `LLM_MODEL_NAME`과 `dummy` 인증을 사용합니다.
2. Triplet의 들어오는 관계와 나가는 관계를 모두 고려해 확장합니다. 재방문한 테이블은 건너뛰고, 새 테이블이 없으면 조기 종료합니다.
3. 시작 테이블을 포함한 전체 확장 후보를 카탈로그 순서대로 중복 없이 반환합니다. 원본 카탈로그의 DDL·설명을 그대로 사용하며, 후보를 추가 LLM으로 걸러내거나 개수로 자르지 않습니다.

응답의 `data`는 `table_name`, `schema`, `description`을 키로 갖는 동일 길이의 문자열 리스트입니다. 비어 있지 않은 `data`는 SQL 생성 요청의 `schema`에 그대로 전달할 수 있습니다. `meta_data`에는 모델명, 시작 선정 호출의 토큰 사용량, `seed_count`, `expanded_count`, `retrieved_count`, `graph_depth`를 기록합니다. `expanded_count`와 `retrieved_count`는 같으며 `graph_depth`는 적용된 탐색 상한입니다(`max_hop=-1`이면 `10`).

시작 테이블이 없으면 한 번의 LLM 호출 후 빈 결과를 반환하고, 빈 카탈로그에서는 LLM을 호출하지 않습니다. RDF 미생성은 404, 잘못된 관계 데이터는 409, 설정·카탈로그 오류는 500, 시작 선정 LLM 오류는 502입니다. 오류는 기존 triplet 검색과 같은 응답 구조를 사용하고, 실패 시 부분 스키마를 반환하지 않습니다. 검색 중 원본 파일은 수정하지 않습니다.

```bash
python3 -m pytest tests/test_only_triplet_based_schema.py -q
```

모의 LLM·HTTP를 사용해 단일 호출, 전체 확장 후보 반환, 대화·깊이 설정, 원본 매핑, 전용 OpenAPI 모델, 오류·취소를 검증합니다. 로그 작업명은 `[only_triplet_based_schema]`입니다.

## POST /retrieve/hybrid_only_based_schema

Triplet 임베딩으로 시작 테이블을 선택하고 RDF 관계를 확장하여 도달한 스키마를 모두 반환합니다. 전용 모델은 `HybridOnlyBasedSchemaRequest`, `HybridOnlyBasedSchemaData`, `HybridOnlyBasedSchemaResponse`입니다. `LLM_API_BASE`나 OpenAI API 키 설정 없이 사용할 수 있습니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 현재 자연어 질의. 빈 문자열·공백뿐인 값은 거부합니다. |
| `history` | 선택 메시지 리스트 | 기본값 `[]`. `history[-3:]`과 현재 질의를 결합하여 임베딩합니다. |
| `top_k` | 선택 정수 | 시작 테이블 수의 상한. 기본값 `-1`은 매핑 가능한 시작 후보 전체입니다. 양의 정수 또는 `-1`만 허용합니다. |
| `max_hop` | 선택 정수 | 기본값 `3`. `0`은 시작점만, 양수는 해당 깊이까지, `-1`은 최대 10-hop까지 탐색합니다. |

기존 triplet 요청의 검증 규칙을 적용합니다. 정의되지 않은 필드, 문자열·실수·불리언·`null` 형식의 정수 인자는 422로 거부합니다. 현재 질의는 히스토리 3개 제한에 포함하지 않습니다.

1. `EMBEDDED_TRIPLET/triplets/`의 인덱스를 읽고, `EMBEDDING_API_BASE`와 현재 `embedding_model_name`을 사용하여 질의·최근 대화를 임베딩합니다. 인덱스는 `/retrieve/indexing_triplet`으로 먼저 준비합니다.
2. 모든 triplet을 검색하여 주체·대상 테이블별 최고 유사도를 집계합니다. 중복 테이블을 제거하고 유사도 내림차순, 동점은 이름 오름차순으로 최대 `top_k`개의 시작점을 선택합니다. 카탈로그에 없는 테이블은 제외하며 후보가 부족하면 가능한 개수만 사용합니다.
3. `RDF_TRIPLET/triplets.json`을 우선 읽고, 기본 파일이 없을 때만 `RDF_TRIPLET_OPEN_ROUTER/triplets.json`을 대신 읽습니다. 선택한 파일의 들어오는 관계와 나가는 관계를 모두 `max_hop`만큼 탐색합니다. 방문한 테이블은 건너뛰며 다음 단계에 새 테이블이 없으면 종료합니다. 임베딩 인덱스의 원본 관계 대신 현재 RDF 파일을 탐색하므로, 기본·대체 중 사용할 RDF 파일도 준비되어 있어야 합니다.
4. 시작점을 포함한 전체 확장 결과를 카탈로그 순서대로 반환합니다. DDL·설명은 동일한 원본 카탈로그 스냅샷에서 가져옵니다. 최종 반환 수는 `top_k`보다 많을 수 있습니다. LLM 호출이나 최종 재선별은 수행하지 않습니다.

대체 경로는 그래프 원본을 읽는 데만 적용합니다. 시작점은 기존 `EMBEDDED_TRIPLET` 인덱스와 `EMBEDDING_API_BASE`로 선택하며 OpenRouter API 키나 LLM 호출은 필요하지 않습니다. 기본 RDF가 `[]`이면 시작점만 반환하고 대체하지 않습니다. 잘못된 JSON·RDF 형식·카탈로그 밖 테이블 참조, 권한·읽기 오류나 카탈로그 오류에도 대체하지 않습니다. 실제 선택한 RDF 경로와 기본 파일 누락에 따른 대체 사유는 로그에 기록합니다.

```bash
curl -X POST http://localhost:31065/retrieve/hybrid_only_based_schema \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"학과별 과목 정보를 조회해줘","history":[],"top_k":1,"max_hop":2}'
```

응답의 `data`는 `table_name`, `schema`, `description`을 키로 갖는 동일 길이의 문자열 리스트입니다. 아래 수치와 테이블은 형식 설명용 예시입니다.

```json
{
  "data": {
    "table_name": ["EXAMPLE"],
    "schema": ["CREATE TABLE EXAMPLE (id INTEGER);"],
    "description": ["예제 테이블"]
  },
  "meta_data": [{
    "embedding_model": "BAAI/bge-m3",
    "top_k": 1,
    "seed_count": 1,
    "expanded_count": 1,
    "retrieved_count": 1,
    "graph_depth": 2,
    "unmapped_table_count": 0
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.1
}
```

`expanded_count`와 `retrieved_count`는 같고 `graph_depth`는 적용한 탐색 상한입니다(`max_hop=-1`이면 `10`). `unmapped_table_count`는 임베딩 인덱스에 있지만 현재 카탈로그에 없는 고유 테이블 수입니다. 유효한 빈 인덱스나 매핑 가능한 시작점이 없는 경우 질의 임베딩 없이 빈 리스트를 반환하지만 카탈로그·RDF는 로드·검증합니다. 인덱스 누락 또는 기본·대체 RDF 파일 모두 누락은 404, 인덱스 손상·모델/차원 불일치·선택한 RDF의 형식·테이블 참조 오류는 409, 임베딩 호출·벡터 오류는 502, 설정·카탈로그·파일 읽기 오류는 500입니다. 검색 중 저장 파일은 수정하지 않습니다.

```bash
python3 -m pytest tests/test_hybrid_only_based_schema.py -q
```

## POST /retrieve/hybrid_only_based_schema_open_ai

OpenAI triplet 임베딩으로 시작 테이블을 선택하고 OpenAI용 RDF 관계를 확장하여 모든 후보의 원본 스키마를 반환합니다. 기존 `/retrieve/hybrid_only_based_schema`와 같은 검색 흐름이며 전용 모델은 `HybridOnlyBasedSchemaOpenAiRequest`, `HybridOnlyBasedSchemaOpenAiData`, `HybridOnlyBasedSchemaOpenAiResponse`입니다.

요청은 필수 `query`, 선택 `history`(기본 `[]`), `top_k`(기본 `-1`), `max_hop`(기본 `3`)입니다. `history[-3:]`과 현재 질의를 임베딩하고 테이블별 최고 triplet 유사도 순으로 시작점을 선택합니다. 동점은 이름 오름차순으로 처리합니다. `top_k`는 시작 테이블 수의 상한이며 `-1`이면 매핑 가능한 후보 전체를 선택하고, 양수이면 최대 k개를 선택합니다. 부족하면 가능한 수만 사용합니다.

설정은 `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL_NAME`, `EMBEDDED_TRIPLET_OPEN_AI`, `RDF_TRIPLET_OPEN_AI`를 사용합니다. 임베딩은 공식 주소 `https://api.openai.com/v1`에서 키로 인증하여 호출합니다. 인덱스는 `<EMBEDDED_TRIPLET_OPEN_AI>/triplets/`, 탐색 관계는 `<RDF_TRIPLET_OPEN_AI>/triplets.json`에서 읽으며 상대 경로는 프로젝트 루트 기준입니다. `/retrieve/generate_triplet_open_ai`로 관계를 준비하고 `/retrieve/indexing_triplet_open_ai`로 인덱싱한 뒤 검색합니다.

시작점에서 RDF 관계를 양방향으로 탐색합니다. `max_hop=0`이면 시작점만, 양수이면 해당 깊이까지, `-1`이면 방문 테이블을 건너뛰며 최대 10-hop까지 탐색하고 새 테이블이 없으면 종료합니다. LLM을 호출하지 않으므로 `OPENAI_LLM_MODEL_NAME`이나 `LLM_API_BASE` 설정은 필요하지 않습니다.

```bash
curl -X POST http://localhost:31065/retrieve/hybrid_only_based_schema_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"학과별 과목 정보를 조회해줘","history":[],"top_k":1,"max_hop":2}'
```

응답 `data`는 `table_name`, `schema`, `description`을 키로 갖는 동일 길이의 문자열 리스트입니다. 시작점을 포함한 전체 확장 결과를 원본 카탈로그 순서대로 반환하며 최종 반환 수에는 `top_k`를 적용하지 않습니다. DDL·설명은 현재 카탈로그 스냅샷에서 가져옵니다. `meta_data`는 일반 hybrid 검색과 같은 `embedding_model`, `top_k`, `seed_count`, `expanded_count`, `retrieved_count`, `graph_depth`, `unmapped_table_count`를 기록하며 `embedding_model`은 OpenAI 임베딩 모델명입니다.

키·임베딩 모델과 일반/OpenAI 저장 경로 분리는 검색 전에 검증합니다. OpenAI 인덱스 경로가 일반 인덱스 경로와 같거나 OpenAI RDF 경로가 일반 RDF 경로와 같으면 500을 반환합니다. 인덱스·RDF 누락은 404이며 대응하는 OpenAI 엔드포인트를 안내합니다. 손상·모델/차원 불일치·잘못된 RDF는 409, 임베딩 오류는 502, 기타 설정·카탈로그 오류는 500입니다. 입력 검증 오류는 422입니다. 오류 응답과 로그의 API 키를 마스킹합니다. 유효한 빈 인덱스나 매핑 가능한 시작점이 없으면 임베딩 호출 없이 빈 결과를 반환합니다.

```bash
python3 -m pytest tests/test_hybrid_only_based_schema_open_ai.py -q
```

테스트는 모의 키·HTTP 전송으로 OpenAI 인증과 모델·최근 대화 전달, 저장 경로 분리, 시작점 제한·multi-hop 경계, 전용 스키마, 오류·키 마스킹을 검증합니다. 로그 작업명은 `[hybrid_only_based_schema_open_ai]`입니다.

## POST /retrieve/triplet_based_schema_open_ai

OpenAI API 키를 사용하여 질의·최근 대화에 필요한 테이블 스키마를 검색합니다. 기존 triplet 검색과 같은 **전체 카탈로그에서 후보 선택 → 양방향 관계 확장 → 원본 DDL 기반 재선별 → 원본 스키마 반환** 흐름이며, 임베딩 서버나 FAISS 인덱스는 필요하지 않습니다. 요청의 `max_hop`을 사용하며 기본 3단계, `-1`은 재방문 테이블을 건너뛰며 최대 10단계 탐색합니다. 응답의 `graph_depth`도 적용된 상한을 표시합니다.

전용 모델은 `TripletBasedSchemaOpenAiRequest`, `TripletBasedSchemaOpenAiData`, `TripletBasedSchemaOpenAiResponse`입니다. 요청은 필수 `query`, 선택 `history`, `max_hop`(기본값 `3`), `top_k`(기본값 `-1`)를 받으며, 두 LLM 단계에 최근 `history[-3:]`과 현재 질의를 전달합니다. 시작 테이블은 `top_k`가 양수이면 최대 k개, 생략 또는 `-1`이면 제한 없이 선택합니다. 이름 검증·중복 제거 후 제한하며 최종 LLM 재선별 개수에는 적용하지 않습니다. 검증 규칙은 기존 triplet 검색과 같고, 정의되지 않은 필드나 잘못된 질의·히스토리·탐색 깊이·시작점 수는 422로 거부합니다.

설정은 `.env`의 `OPENAI_API_KEY`, `OPENAI_LLM_MODEL_NAME`, `RDF_TRIPLET_OPEN_AI`를 사용합니다. 환경변수명은 모두 대문자이며 `RDF_TRIPLET_open_ai`는 별칭으로 지원하지 않습니다. LLM은 공식 주소 `https://api.openai.com/v1`에 키로 인증하여 호출하고 `LLM_API_BASE` 설정은 사용하지 않습니다. 기존 OpenAI 호출 옵션을 재사용하며 기본 모델 `gpt-6-luna`에는 `reasoning_effort="none"`을 적용합니다. 호출 제한 시간은 60초, SDK 재시도는 최대 2회입니다.

원본 카탈로그는 기존 `dataset_path` 설정을 사용하고, 관계 파일은 `<RDF_TRIPLET_OPEN_AI>/triplets.json`에서 읽습니다. 상대 경로는 프로젝트 루트 기준입니다. RDF 파일이 없으면 먼저 `/retrieve/generate_triplet_open_ai`를 실행합니다. 기존 `RDF_TRIPLET` 파일로 대체하거나 검색 중 관계를 생성하지 않습니다. 두 저장 경로가 같으면 500 오류를 반환합니다.

```bash
curl -X POST http://localhost:31065/retrieve/triplet_based_schema_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"그중 2학기 과목만 조회해줘","history":[{"role":"user","content":"학과별 과목 정보를 조회하고 싶어"}],"top_k":1}'
```

응답 예시는 형식 설명용입니다. `data`의 세 리스트는 같은 순서·길이이며 원본 카탈로그의 DDL·설명을 그대로 반환합니다. `meta_data`의 토큰 수는 실제 수행한 LLM 호출의 합계이고, `expanded_count`는 시작 후보를 포함한 전체 확장 후보 수입니다.

```json
{
  "data": {
    "table_name": ["TABLE_A", "TABLE_B"],
    "schema": ["CREATE TABLE TABLE_A (id INTEGER);", "CREATE TABLE TABLE_B (id INTEGER);"],
    "description": ["첫 번째 테이블 설명", "두 번째 테이블 설명"]
  },
  "meta_data": [{
    "model_name": "gpt-6-luna",
    "question_token": 1200,
    "answer_token": 150,
    "seed_count": 1,
    "expanded_count": 4,
    "retrieved_count": 2,
    "graph_depth": 3
  }],
  "status": "success",
  "msg": "success",
  "elapsed_time": 1.2
}
```

RDF에 없는 독립 테이블도 전체 카탈로그에서 선택할 수 있습니다. 후보 수 제한은 없으며 최종 선택 순서대로 이름을 중복 제거합니다. 시작 후보가 없으면 두 번째 LLM 호출을 생략하고, 빈 카탈로그에서는 모든 LLM 호출을 생략합니다. 키·모델·저장 경로 설정은 빈 입력에서도 먼저 검증합니다. 검색 결과가 없으면 200과 세 개의 빈 리스트를 반환합니다. 비어 있지 않은 `data`는 SQL 생성 요청의 `schema`에 그대로 전달할 수 있습니다.

키·모델·경로·카탈로그 오류는 500, RDF 미생성은 404, RDF 손상·원본에 없는 테이블 참조는 409, OpenAI 인증·한도·호출 실패 또는 잘못된 선택 응답은 502입니다. 오류 응답은 `data: null`, `meta_data: []`, `status: "error"`, `msg`, `elapsed_time` 형식입니다. 실패 시 부분 결과를 반환하지 않습니다. API 키는 오류 응답과 로그·예외 추적에서 마스킹합니다.

검색 중 파일은 수정하지 않습니다. 로그는 `[triplet_based_schema_open_ai]`와 요청별 `request_id`로 구분하며, 단계별 건수와 소요 시간을 기록합니다.

```bash
python3 -m pytest tests/test_triplet_based_schema_open_ai.py -q
```

테스트는 모의 키·LLM·HTTP 전송과 임시 저장소를 사용하여 전용 스키마, OpenAI 인증·모델·호출 주소, 양방향 관계 확장, 원본 매핑·대화 문맥, 저장소 분리, 오류·키 마스킹·취소와 기존 검색의 호환성을 확인합니다. 실제 OpenAI 서버는 호출하지 않습니다.

## POST /retrieve/only_triplet_based_schema_open_ai

시작 테이블 선정에 OpenAI LLM을 한 번 사용하고, RDF 그래프로 확장한 전체 스키마를 반환합니다. 최종 LLM 재선별은 생략합니다. 전용 모델은 `OnlyTripletBasedSchemaOpenAiRequest`, `OnlyTripletBasedSchemaOpenAiData`, `OnlyTripletBasedSchemaOpenAiResponse`입니다.

요청은 필수 `query`, 선택 `history`, `max_hop`, `top_k`입니다. 시작 테이블 선정에 현재 질의와 `history[-3:]`, 전체 테이블명·설명을 전달합니다. `max_hop` 기본값은 `3`, `0`은 시작 테이블만 사용하고 `-1`은 재방문을 건너뛰며 최대 10-hop까지 탐색합니다. 새로운 테이블이 없으면 조기 종료합니다. `max_hop`은 `-1` 또는 0 이상의 정수여야 하며 정의되지 않은 필드와 잘못된 입력은 422로 거부합니다.

`top_k`는 확장 전 시작 테이블 개수의 상한입니다. 기본값은 `-1`이며 생략하거나 `-1`이면 기존처럼 제한 없이 선택합니다. 양수이면 LLM이 관련도 순서대로 최대 k개의 시작 테이블을 선택하며, 관련 테이블이 부족하면 적게 선택할 수 있습니다. 응답의 테이블명을 검증하고 중복을 제거한 뒤 초과한 시작점은 LLM 응답 순서대로 k개까지만 사용합니다. `0`, `-1`보다 작은 음수, 문자열·실수·불리언·`null`은 422로 거부합니다. 네 triplet 검색 엔드포인트가 같은 시작점 제한 규칙을 사용합니다.

```bash
curl -X POST http://localhost:31065/retrieve/only_triplet_based_schema_open_ai \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":"학과별 과목 정보를 조회해줘","history":[],"max_hop":2,"top_k":1}'
```

설정은 `.env`의 `OPENAI_API_KEY`, `OPENAI_LLM_MODEL_NAME`, `RDF_TRIPLET_OPEN_AI`를 사용합니다. 공식 OpenAI API 주소와 기존 OpenAI 모델 옵션을 재사용합니다. 카탈로그는 `dataset_path`의 파일, 관계는 `<RDF_TRIPLET_OPEN_AI>/triplets.json`에서 읽습니다. RDF 파일이 없으면 `/retrieve/generate_triplet_open_ai`를 먼저 실행합니다. `RDF_TRIPLET_OPEN_AI`는 `RDF_TRIPLET`과 다른 경로여야 하며, 검색 중 일반 triplet 파일로 대체하거나 관계를 생성하지 않습니다. 임베딩 인덱스는 필요하지 않습니다.

제한을 적용한 시작 테이블에서 양방향으로 확장한 후보 전체를 카탈로그 순서대로 중복 없이 반환합니다. 따라서 반환 테이블 수는 `top_k`보다 많을 수 있습니다. `data`는 원본 `table_name`, `schema`, `description`의 동일 길이 리스트입니다. `seed_count`는 제한 적용 후 실제 시작 테이블 수이며, `expanded_count`와 `retrieved_count`는 같습니다. 토큰은 시작 테이블 선정 호출만 집계하고 추가 LLM 호출은 하지 않습니다. `graph_depth`는 적용된 탐색 상한이며 `max_hop=-1`이면 `10`입니다.

시작 테이블이 없으면 빈 결과를 반환하고, 빈 카탈로그에서는 OpenAI를 호출하지 않습니다. 키·모델·저장 경로 설정은 빈 입력에서도 먼저 검증합니다. RDF 미생성은 404, 잘못된 관계 데이터는 409, 설정·카탈로그 오류는 500, OpenAI 호출·선정 응답 오류는 502입니다. 오류는 기존 OpenAI triplet 검색과 같은 응답 구조를 사용하고 키를 로그·오류 응답에서 마스킹합니다. 검색 중 원본 파일은 수정하지 않습니다.

```bash
python3 -m pytest tests/test_only_triplet_based_schema_open_ai.py -q
```

테스트는 모의 키·OpenAI 응답·HTTP 전송으로 단일 호출과 전체 후보 반환, 공식 주소·인증·모델 옵션, 전용 RDF 경로·스키마, multi-hop 경계, 오류·키 마스킹·취소를 검증합니다. 실제 OpenAI API는 호출하지 않습니다. 로그 작업명은 `[only_triplet_based_schema_open_ai]`입니다.

## POST /retrieve/generate_triplet

요청 본문 없이 호출하면 기본 입력인 `dataset/db_schema.json`의 전체 테이블을 읽고, 테이블명·DDL·설명을 한 번의 LLM 요청에 전달하여 테이블 간 관계를 생성합니다. 입력 파일은 `--dataset_path`로 변경할 수 있습니다. 저장된 임베딩 인덱스와 OpenAI API 키는 필요하지 않습니다.

```bash
curl -X POST http://localhost:31065/retrieve/generate_triplet
```

`.env`의 `LLM_API_BASE`, `LLM_MODEL_NAME`과 기존 SQL 생성의 호출 설정을 사용합니다. 기본 온도는 0이며 DeepSeek는 thinking을 비활성화하고, 요청 제한 시간은 60초, SDK 재시도 횟수는 최대 2회입니다. LLM 서버는 JSON Schema 구조화 응답과 전체 입력을 처리할 수 있는 컨텍스트 크기를 지원해야 합니다. 컨텍스트 초과·시간 초과는 LLM 오류로 반환하며 입력을 임의로 줄이지 않습니다.

현재 기본 데이터에는 명시적인 외래 키 선언이 없으므로 컬럼·키·설명에 근거하여 소속·위치·참조 등의 의미 관계도 추론합니다. 관계 표현은 주체에서 객체로 읽는 짧은 영문 표현입니다. 이는 LLM이 스키마를 근거로 추론한 관계이며 실제 데이터베이스의 외래 키 검증 결과는 아닙니다.

LLM은 주체 테이블명·관계·객체 테이블명만 반환하고 서버가 이름을 검증한 후 **원본 설명을 그대로** 붙입니다. 동일한 주체·관계·객체 조합은 중복 제거하고 방향은 유지합니다. 응답 모델은 `GenerateTripletResponse`이며, `data`의 각 항목은 `[주체 객체, 관계 문자열, 대상 객체]`의 세 원소 배열입니다. 아래 설명·관계·수치는 형식 설명용 예시이며 실제 설명은 데이터셋 원문으로 반환합니다.

```json
{
  "data": [
    [
      {"table_name": "SUBJECT_SELECTOR", "description": "원본 SUBJECT_SELECTOR 설명"},
      "references",
      {"table_name": "SUBJECT_SUMMARY", "description": "원본 SUBJECT_SUMMARY 설명"}
    ]
  ],
  "meta_data": [
    {
      "model_name": "DeepSeek-V4-Flash-0731",
      "question_token": 32000,
      "answer_token": 50,
      "table_count": 97,
      "triplet_count": 1,
      "triplet_file": "/app/text_to_sql_api/rdf_triplet/triplets.json"
    }
  ],
  "status": "success",
  "msg": "success",
  "elapsed_time": 3.2
}
```

저장 경로는 `.env`의 `RDF_TRIPLET=./rdf_triplet`로 관리합니다. 상대 경로는 API 프로젝트 루트 기준이며 `<RDF_TRIPLET>/triplets.json`에는 응답 `data`와 동일한 목록만 UTF-8 JSON으로 저장합니다. 내부 `RdfTripletStore.load()`도 같은 경로를 사용하며 별도 조회 엔드포인트는 제공하지 않습니다.

호출할 때마다 최신 전체 데이터로 다시 생성하고 성공했을 때 파일을 교체합니다. 관계가 없으면 `data: []`를 반환하고 빈 목록으로 저장합니다. 입력이 빈 목록이면 LLM 호출을 생략합니다. 기존 파일은 새 결과의 검증과 임시 파일 작성이 끝난 뒤 원자적으로 교체되므로 실패 시 보존됩니다. 동시 요청은 마지막으로 저장을 완료한 결과가 남으며 읽는 쪽에는 완성된 JSON만 노출됩니다. LLM 처리 중 취소하면 저장하지 않고, 이미 시작한 원자적 저장은 완료한 뒤 취소를 처리합니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | 생성·저장 성공. 관계가 없는 경우도 포함 |
| 500 | 설정, 데이터 파일·스키마 또는 저장 오류 |
| 502 | LLM 호출 실패, 불완전·거절·잘못된 응답 또는 원본에 없는 테이블명 |

오류도 `data: null`, `status: "error"`, `msg`, `elapsed_time`의 공통 응답을 사용합니다. 로그는 `[generate_triplet]`과 `request_id`로 구분하며 스키마 로드, LLM 호출, 결과 검증, 저장·완료 단계와 건수·처리 시간을 출력합니다.

```bash
python3 -m pytest tests/test_generate_triplet.py -q
```

테스트는 모의 LLM을 사용하여 출력·저장·실패 시 파일 보존과 기존 클라이언트의 구조화 응답 요청을 확인하며 실제 모델을 호출하지 않습니다.

## POST /retrieve/generate_triplet_open_ai

요청 본문 없이 전체 데이터셋의 테이블명·DDL·설명을 OpenAI에 전달하여 관계를 생성합니다. `.env`의 `OPENAI_API_KEY`, `OPENAI_LLM_MODEL_NAME`, `RDF_TRIPLET_OPEN_AI`를 사용하며 기존 임베딩 서버나 인덱싱·검색을 먼저 호출할 필요는 없습니다.

```bash
curl -X POST http://localhost:2711/retrieve/generate_triplet_open_ai
```

위 주소는 호스트 포트 `2711`을 컨테이너의 API 포트에 매핑한 경우의 예시입니다. 실제 공개 포트에 맞춰 변경합니다.

응답은 `/retrieve/generate_triplet`과 같은 `GenerateTripletResponse` 형식입니다. 원본 설명 복원, 테이블명 검증과 중복 제거를 수행하고, `data`와 동일한 목록을 `<RDF_TRIPLET_OPEN_AI>/triplets.json`에 원자적으로 저장합니다. `meta_data`에는 OpenAI 모델·토큰 사용량·테이블 수·관계 수와 실제 저장 경로를 반환합니다. 빈 관계 목록도 성공이며, 실패하면 이전 파일을 보존합니다. 취소·동시 저장 처리도 기존 triplet과 같습니다.

키·모델·경로·데이터셋·저장 설정 오류는 500, OpenAI 인증·한도·호출 오류나 유효하지 않은 모델 응답은 502로 반환합니다. 키는 로그와 오류 응답에서 마스킹합니다.

```bash
python3 -m pytest tests/test_generate_triplet_open_ai.py -q
```

테스트는 모의 키·응답과 임시 저장소를 사용하며 실제 OpenAI API는 호출하지 않습니다.

## POST /generate_sql/llm_based_generate_sql

현재 자연어 질의, 최근 대화 메시지 최대 3개와 전달받은 테이블 정보를 이용해 PostgreSQL SQL 한 문장을 생성하고 `data`에 문자열로 반환합니다. 검색 API 응답의 `data`를 이 API의 `schema`에 그대로 전달합니다. SQL 생성 API는 전달받은 스키마를 사용하므로 로컬 인덱스나 데이터셋 파일이 없어도 호출할 수 있습니다.

전용 요청·응답 모델은 `LlmBasedGenerateSqlRequest`, `SqlGenerationSchema`, `LlmBasedGenerateSqlResponse`입니다.

| 요청 필드 | 형식 | 설명 |
| --- | --- | --- |
| `query` | 필수 문자열 | 사용자의 현재 자연어 질의. 빈 문자열·공백뿐인 값은 허용하지 않습니다. |
| `history` | 선택 메시지 리스트 | 검색 API와 같은 `role`·`content` 형식. 기본값은 `[]`이며 `history[-3:]`만 사용합니다. |
| `schema` | 필수 객체 | `table_name`, `schema`, `description`의 세 문자열 리스트. 테이블이 하나 이상이고 모든 항목이 공백뿐이지 않으며 리스트 길이가 같아야 합니다. |

메시지 하나가 1 turn입니다. 현재 질의는 과거 3개 제한에 포함하지 않습니다. LLM에는 PostgreSQL SQL 생성 지시, 최근 메시지의 역할·내용, 현재 질의와 전체 입력 스키마를 전달합니다. 입력 DDL에 `VARCHAR2`, `NUMBER` 등 Oracle 형식이 있어도 출력 SQL은 PostgreSQL 문법을 사용하도록 지시합니다.

요청 예시를 `sql_request.json`으로 저장한 뒤 호출할 수 있습니다.

```json
{
  "query": "학과별 수강 인원 합계를 조회해줘",
  "history": [
    {"role": "user", "content": "교과목의 수강 인원을 보고 싶어"},
    {"role": "assistant", "content": "교과목 요약 정보를 기준으로 조회할 수 있습니다."}
  ],
  "schema": {
    "table_name": ["SUBJECT_SUMMARY"],
    "schema": [
      "CREATE TABLE SUBJECT_SUMMARY (DEPARTMENT_NAME VARCHAR2, SUBJECT_ENROLLMENT_NUMBER NUMBER);"
    ],
    "description": ["학과별 교과목 수강 인원 요약 정보"]
  }
}
```

```bash
curl -X POST http://localhost:31065/generate_sql/llm_based_generate_sql \
  -H 'Content-Type: application/json' \
  --data-binary @sql_request.json
```

생성 SQL·토큰 수·시간은 설명용 예시입니다. `question_token`과 `answer_token`은 LLM이 반환한 입력·출력 토큰 수이며 사용량이 없으면 0입니다.

```json
{
  "data": "SELECT DEPARTMENT_NAME, SUM(SUBJECT_ENROLLMENT_NUMBER) AS TOTAL_ENROLLMENT FROM SUBJECT_SUMMARY GROUP BY DEPARTMENT_NAME;",
  "meta_data": [
    {
      "model_name": "DeepSeek-V4-Flash-0731",
      "question_token": 120,
      "answer_token": 35
    }
  ],
  "status": "success",
  "msg": "success",
  "elapsed_time": 1.2
}
```

예를 들어 검색 스크립트가 저장한 응답을 이용하려면 다음과 같이 생성 요청을 구성합니다. 실제 대화가 있으면 `history`에도 같은 메시지 리스트를 전달합니다.

```bash
jq --arg query '학과별 수강 인원 합계를 조회해줘' \
  '{query: $query, history: [], schema: .data}' \
  embedding_based_schema_response_1.json > sql_request.json
```

기본 생성 온도는 0이며 DeepSeek 모델은 참조 코드처럼 thinking을 비활성화합니다. LLM 호출은 비동기로 수행하고 요청 제한 시간은 60초, SDK 재시도 횟수는 최대 2회입니다. SQL은 생성·반환하며 데이터베이스에서 실행하지 않습니다. 응답 검증은 SQL 문자열과 LLM 응답 형식을 대상으로 합니다.

| HTTP 상태 | 의미 |
| --- | --- |
| 200 | SQL 생성 성공 |
| 422 | 필수 필드 누락, 잘못된 질의·히스토리·스키마, 빈 테이블 목록 또는 리스트 길이 불일치 |
| 500 | LLM URL·모델 설정 또는 로컬 처리 오류 |
| 502 | LLM 호출 실패·타임아웃·거절·중단, 잘못된 구조화 응답 또는 빈 SQL |

500·502는 `data=null`, `meta_data=[]`, `status="error"`, `msg`에 원인을 담은 공통 응답입니다. 422는 FastAPI 기본 `detail` 응답입니다.

## 인덱싱·검색·SQL 생성 진행 로그

`python3 main.py`와 `uvicorn main:app` 모두 기본 INFO 수준의 진행 로그를 서버 콘솔의 표준 오류(stderr)에 출력합니다. 로그에는 시각, 수준, 요청별 `request_id`, 현재 처리 단계인 `stage`가 포함됩니다. 동시에 요청하더라도 같은 `request_id`로 처리 흐름을 확인할 수 있습니다.

스키마 로드·검증, 저장소 잠금 대기·획득, 기존 인덱스 로드, 신규 테이블 선별, 임베딩, 인덱스 추가·저장, 요청 종료를 기록합니다. 잠금 대기·임베딩·저장·전체 처리 시간과 대상 수·차원·모델·경로를 함께 출력합니다. 신규 테이블이 없으면 임베딩과 저장을 생략했다는 로그를 남깁니다. 설명·DDL·벡터 전체를 로그로 덤프하지 않습니다.

인덱싱 로그는 `[indexing_schema]`, 검색 로그는 `[embedding_based_schema]`로 구분합니다. 검색 시 공유 잠금 대기, 인덱스 로드, 질의 임베딩, 검색 결과 테이블 이름·개수와 소요 시간을 출력합니다. 히스토리 전체 개수(`history_count`), 사용한 메시지 수(`used_history_count`, 최대 3), 결합한 검색 문자열 길이(`embedding_text_length`)도 기록합니다.

SQL 생성 로그는 `[llm_based_generate_sql]`로 구분합니다. 요청 식별자와 히스토리·테이블 수, LLM 호출 모델·소요 시간, SQL 응답 형식 검증과 전체 처리 결과를 기록합니다. 실패 시 현재 단계와 오류 원인을 출력합니다.

아래는 일부 단계의 출력 예시이며 식별자, 경로, 수치, 시간은 실행마다 달라집니다.

```text
2026-11-02 14:00:00 INFO [indexing_schema] request_id=4f70b18e653f4b08a705738f44f369c0 stage=request 요청 시작 method=POST path=/retrieve/indexing_schema
2026-11-02 14:00:00 INFO [indexing_schema] request_id=4f70b18e653f4b08a705738f44f369c0 stage=table_selection 신규 테이블 선별 완료 added=1 skipped=96 added_tables=['EXAMPLE']
2026-11-02 14:00:00 INFO [indexing_schema] request_id=4f70b18e653f4b08a705738f44f369c0 stage=embedding 임베딩 요청 시작 model=BAAI/bge-m3 target_count=1
2026-11-02 14:00:01 INFO [indexing_schema] request_id=4f70b18e653f4b08a705738f44f369c0 stage=embedding_validation 임베딩·정규화 완료 vector_count=1 dimension=1024 elapsed=1.00s
2026-11-02 14:00:01 INFO [indexing_schema] request_id=4f70b18e653f4b08a705738f44f369c0 stage=index_save 인덱스·메타데이터·원본 저장 완료 directory=/project/text_to_sql_api/embedded_schema/db_schema total=97 elapsed=0.01s
2026-11-02 14:00:01 INFO [indexing_schema] request_id=4f70b18e653f4b08a705738f44f369c0 stage=complete 요청 완료 http_status=200 added=1 skipped=96 total=97 elapsed=1.25s
```

실패 시 현재 단계, HTTP 상태, 원인, 전체 소요 시간을 기록합니다. 인덱스 충돌(409)은 WARNING, 서버·임베딩 오류(500·502)는 ERROR와 예외 추적을 출력합니다. 요청 취소 시 작업 종료 대기와 최종 작업 결과를 WARNING으로 기록합니다. API 응답 형식은 동일합니다.

## 테스트

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

임시 디렉터리와 대체 임베딩 클라이언트, HTTP 모의 전송을 사용하므로 실제 임베딩 서버에 연결하거나 운영 인덱스를 변경하지 않습니다. 인덱싱의 최초 생성, 97개 입력, 재호출, 추가·변경·삭제 입력, 재시작, 동시 호출, 취소, 저장 복구와 오류 응답을 검증합니다. 검색은 Top-k 순위·DDL 매핑, 입력 검증, 오류 응답, 실제 클라이언트의 질의 전송 형식, 동시 읽기와 쓰기의 스냅샷 일관성을 검증합니다. 히스토리 생략·빈 리스트의 호환성과 최근 3개 메시지 선택, 역할·내용·순서 보존, 히스토리가 반영된 검색 결과도 확인합니다.

SQL 생성 테스트도 실제 LLM 서버를 호출하지 않습니다. PostgreSQL 프롬프트·스키마·최근 히스토리 전달, SDK 구조화 응답과 사용량, 설정 우선순위, 입력 검증과 LLM 오류·취소 및 클라이언트 종료를 검증합니다. SQL의 실제 실행 결과는 검증하지 않습니다.

OpenAI 테스트는 모의 키·HTTP 전송과 임시 인덱스를 사용합니다. 공식 URL과 Bearer 인증, 선택한 모델·호출 옵션, 증분 인덱싱·검색·SQL 생성, 두 저장소의 분리, 동시 인덱싱과 설정 오류, 키 마스킹을 검증하며 실제 OpenAI API는 호출하지 않습니다.

### 실행 중인 서버 API 호출

`tests/scripts/indexing_schema_script.sh`는 실행 중인 서버에 `POST /retrieve/indexing_schema`를 한 번 호출합니다. 요청 본문이나 입력 JSON 인자는 필요하지 않습니다. 서버가 자신의 설정에 따라 스키마를 읽고 실제 임베딩과 인덱스 저장을 수행합니다. Bash, `curl`, `jq`, `date`, `awk`, `mkdir`, `dirname`이 필요합니다.

`tests/scripts/`의 모든 스크립트는 기본적으로 프로젝트의 `tests/results/`에 결과를 저장합니다. 스크립트 위치를 기준으로 경로를 계산하므로 실행 디렉터리에 관계없이 같은 위치를 사용하며, 디렉터리가 없으면 자동으로 생성합니다. `OUTPUT_DIR`을 지정하면 해당 경로를 사용하고, 상대 경로는 실행 디렉터리를 기준으로 해석합니다.

`text_to_sql_api` 디렉터리에서 실행합니다.

```bash
bash tests/scripts/indexing_schema_script.sh
```

기본 서버 주소는 `http://localhost:31065`이며, 서버 주소와 응답 저장 위치를 환경변수로 변경할 수 있습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR=./test_results \
  bash tests/scripts/indexing_schema_script.sh
```

요청 시각, HTTP 상태, 응답 소요 시간, 전체 처리 시간과 추가·건너뜀·전체 테이블 수를 출력합니다. 기본적으로 `tests/results/`에 응답 원문 `indexing_schema_raw_1.json`과 정리한 JSON `indexing_schema_response_1.json`을 저장하며, 같은 경로의 파일은 실행마다 갱신합니다.

연결 실패, HTTP 오류, JSON 파싱 실패 또는 API의 오류 상태에는 0이 아닌 종료 코드를 반환합니다. 서버가 반환한 오류 응답도 원문 파일에 보존합니다. `--help`로 사용법을 확인할 수 있습니다.

### Triplet 증분 인덱싱 호출

기본 인덱싱의 입력 `<RDF_TRIPLET>/triplets.json`과 OpenAI 인덱싱의 입력 `<RDF_TRIPLET_OPEN_AI>/triplets.json`을 준비하고 서버를 실행한 뒤 다음 스크립트를 각각 실행합니다. 기본 인덱싱은 기본 파일이 없을 때만 `<RDF_TRIPLET_OPEN_ROUTER>/triplets.json`을 대신 읽습니다. 사용할 RDF 파일이 없으면 대응하는 triplet 생성 API로 생성하거나 같은 배열 형식으로 준비합니다. 기본 인덱싱에는 `EMBEDDING_API_BASE`, `EMBEDDED_TRIPLET`을, OpenAI 인덱싱에는 `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL_NAME`, `EMBEDDED_TRIPLET_OPEN_AI`를 서버에 설정합니다.

```bash
bash tests/scripts/indexing_triplet_script.sh
bash tests/scripts/indexing_triplet_open_ai_script.sh
```

각 스크립트는 대응하는 `/retrieve/indexing_triplet`, `/retrieve/indexing_triplet_open_ai`에 요청 본문 없이 한 번 POST합니다. 요청 JSON 인자는 받지 않습니다. 실제 임베딩과 인덱스 갱신은 서버가 수행하고, 스크립트는 API 키나 `.env`를 읽거나 전달하지 않습니다. 관계 생성·검색·SQL 생성은 자동 호출하지 않습니다.

`BASE_URL`(기본 `http://localhost:31065`), `OUTPUT_DIR`(기본 스크립트 기준 `../results`, 프로젝트의 `tests/results/`)와 `-h`·`--help`를 지원합니다. 필요 명령은 Bash, `curl`, `jq`, `date`, `awk`, `mkdir`, `dirname`입니다. 출력 경로는 공백을 포함할 수 있으며 다른 디렉터리에서도 실행할 수 있습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR='./triplet index results' \
  bash tests/scripts/indexing_triplet_script.sh

BASE_URL=http://localhost:2711 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/indexing_triplet_open_ai_script.sh
```

요청 시각·HTTP 상태·응답 시간·전체 처리 시간과 추가·건너뜀·전체 관계 수, 추가된 triplet ID를 출력합니다. 성공 응답에서 개수가 0 이상 정수인지, 전체 개수가 추가 개수 이상인지 확인합니다. `added_triplet_ids`는 중복 없는 64자리 소문자 SHA-256 문자열 리스트이고 길이가 추가 개수와 같아야 합니다. 빈 입력이나 새 관계가 없어 추가 개수가 0인 결과도 성공입니다.

| 스크립트 | 응답 원문 | 정리한 응답 |
| --- | --- | --- |
| `indexing_triplet_script.sh` | `indexing_triplet_raw_1.json` | `indexing_triplet_response_1.json` |
| `indexing_triplet_open_ai_script.sh` | `indexing_triplet_open_ai_raw_1.json` | `indexing_triplet_open_ai_response_1.json` |

파일은 `OUTPUT_DIR`에 저장하며 요청 전에 이전 결과를 비우고 갱신합니다. 명령 누락, 잘못된 인자, 전송·HTTP·JSON·API·결과 형식 오류는 비정상 종료하며 수신한 원문은 보존합니다.

### 히스토리를 포함한 스키마 검색 호출

서버를 실행하고 `/retrieve/indexing_schema`로 인덱스를 생성한 뒤, `text_to_sql_api` 디렉터리에서 다음 스크립트를 실행합니다.

```bash
bash tests/scripts/embedding_based_schema_script.sh
```

기본 요청 파일은 `tests/test_doc/embedding_based_schema.json`입니다. 스크립트 위치를 기준으로 찾으므로 다른 디렉터리에서도 실행할 수 있습니다. 선택 인자로 다른 요청 JSON을 전달할 수 있고, `BASE_URL`과 `OUTPUT_DIR`로 서버와 결과 저장 위치를 변경할 수 있습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR=./search_results \
  bash tests/scripts/embedding_based_schema_script.sh

bash tests/scripts/embedding_based_schema_script.sh ./my_request.json
```

기본 요청은 `dataset/db_schema.json`의 다음 내용을 참고하여 작성했습니다.

- `SUBJECT_SUMMARY`: 학기·학과별 과목명, 강의·실험·준비·전체 학점과 수강 인원
- `COURSE_CATALOG_SUBJECT_OFFERED`: 학년도·학기별 개설 교과목과 소속 학과, 학점 구성
- `ACADEMIC_TERMS`: 학사 연도와 학기 코드·설명

대화에서 과목·학과, 학점 구성과 수강 인원으로 조회 범위를 구체화하고, 현재 질의에서 학사 연도·학기별 조회에 필요한 테이블을 묻습니다. `top_k`는 3이며, 히스토리를 4개 전달하여 서버가 최근 3개 메시지만 검색에 사용하는 흐름을 확인할 수 있습니다. 관련 테이블은 결과 검토의 참고 대상이며 실제 반환 순위는 임베딩 모델에 따라 달라집니다.

스크립트는 한 번 POST하고 요청 시각, HTTP 상태와 처리 시간을 출력합니다. 성공 응답의 `table_name`, `schema`, `description`이 같은 길이의 문자열 리스트인지, 결과 수가 요청한 `top_k` 이하인지 확인한 뒤 순위별 테이블 이름·DDL·설명을 출력합니다. 빈 결과도 유효한 응답으로 처리합니다.

원문은 `embedding_based_schema_raw_1.json`, 정리한 JSON은 `embedding_based_schema_response_1.json`으로 `tests/results/` 또는 지정한 `OUTPUT_DIR`에 저장합니다. 같은 경로의 파일은 실행마다 갱신합니다. 연결·HTTP·JSON·API 오류와 잘못된 결과 형식에는 0이 아닌 종료 코드를 반환하며, 받은 원문 응답을 보존합니다. `--help`로 사용법을 확인할 수 있습니다.

### Triplet 임베딩 기반 스키마 검색 호출

서버에서 대응하는 triplet 인덱싱을 먼저 완료한 뒤 각 스크립트를 독립적으로 실행합니다. 기본 검색은 `/retrieve/indexing_triplet`이 생성한 `EMBEDDED_TRIPLET` 인덱스를, OpenAI 검색은 `/retrieve/indexing_triplet_open_ai`가 생성한 `EMBEDDED_TRIPLET_OPEN_AI` 인덱스를 사용합니다. 인덱싱과 검색의 임베딩 모델·차원이 같아야 합니다.

```bash
bash tests/scripts/embedded_triplet_based_schema_script.sh
bash tests/scripts/embedded_triplet_based_schema_open_ai_script.sh
```

두 스크립트는 기본 요청 `tests/test_doc/embedding_based_schema.json`을 재사용합니다. `query`, `top_k=3`, 과거 메시지 4개를 전달하며 서버는 최근 `history[-3:]`만 검색에 사용합니다. 선택 인자 `[request.json]`으로 다른 요청을 수정 없이 전달할 수 있고 `-h`·`--help`로 사용법을 확인할 수 있습니다.

`BASE_URL`의 기본값은 두 스크립트 모두 `http://localhost:31065`, `OUTPUT_DIR`의 기본값은 스크립트 위치 기준 `../results`(프로젝트의 `tests/results/`)입니다. 다른 실행 디렉터리에서도 기본 요청·출력 위치를 찾으며 공백 포함 경로를 지원합니다. 사용자 지정 상대 경로는 실행 디렉터리를 기준으로 해석합니다. Bash, `curl`, `jq`, `date`, `awk`, `mkdir`, `dirname`이 필요합니다. OpenAI 키는 서버가 읽습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR='./triplet search results' \
  bash tests/scripts/embedded_triplet_based_schema_script.sh

BASE_URL=http://localhost:2711 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/embedded_triplet_based_schema_open_ai_script.sh ./my_request.json
```

각 스크립트는 한 번 POST하고 요청 시각, HTTP 상태와 요청·전체 처리 시간을 출력합니다. 성공 응답의 `data.table_name`, `data.schema`, `data.description`이 같은 길이의 문자열 리스트인지, 테이블명이 중복되지 않고 반환 개수가 요청의 `top_k` 이하인지 검증한 뒤 순위별 이름·DDL·설명을 출력합니다. 후보가 부족한 결과와 빈 세 리스트도 성공으로 처리합니다.

| 스크립트 | 응답 원문 | 정리한 응답 |
| --- | --- | --- |
| `embedded_triplet_based_schema_script.sh` | `embedded_triplet_based_schema_raw_1.json` | `embedded_triplet_based_schema_response_1.json` |
| `embedded_triplet_based_schema_open_ai_script.sh` | `embedded_triplet_based_schema_open_ai_raw_1.json` | `embedded_triplet_based_schema_open_ai_response_1.json` |

파일은 `OUTPUT_DIR`에 저장하고 요청 전에 같은 이름의 이전 결과를 비웁니다. 요청·응답은 각각 단일 JSON 객체여야 합니다. 명령 누락, 잘못된 인자·요청 파일, 전송·HTTP·JSON·API·결과 형식 오류에는 0이 아닌 종료 코드를 반환하며 수신한 응답 원문을 보존합니다.

### LLM 기반 스키마 검색 호출

서버의 원본 카탈로그와 LLM 설정을 준비한 뒤 각 스크립트를 독립적으로 실행합니다. 기본 검색은 `LLM_API_BASE`, `LLM_MODEL_NAME`을 사용하고 OpenAI 검색은 `OPENAI_API_KEY`, `OPENAI_LLM_MODEL_NAME`을 사용합니다. 인덱싱이나 triplet 생성은 필요하지 않습니다.

```bash
bash tests/scripts/llm_based_schema_script.sh
bash tests/scripts/llm_based_schema_open_ai_script.sh
```

두 스크립트는 공통 기본 요청 `tests/test_doc/llm_based_schema.json`을 사용합니다. 기존 검색 예시와 동일한 질의·대화 4개를 전달하고 `top_k`는 포함하지 않습니다. 서버가 최근 메시지 3개와 현재 질의를 검색에 사용합니다. 기본 요청은 실행 디렉터리와 무관하게 스크립트 위치 기준으로 찾습니다.

선택 인자 `[request.json]`, `--help`, `BASE_URL`(기본 `http://localhost:31065`), `OUTPUT_DIR`(기본 `tests/results/`)를 지원합니다. 사용자 지정 요청은 수정 없이 전달합니다. 스크립트는 애플리케이션 서버만 호출하며 API 키는 읽거나 전달하지 않습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR='./llm search results' \
  bash tests/scripts/llm_based_schema_script.sh

BASE_URL=http://localhost:2711 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/llm_based_schema_open_ai_script.sh ./my_request.json
```

각 스크립트는 대응 API에 한 번 POST하고 요청 시각, HTTP 상태, 요청·전체 처리 시간, 반환 수와 테이블별 이름·DDL·설명을 출력합니다. 성공 응답의 세 문자열 리스트 길이가 같은지 검사하며 빈 결과도 성공입니다. 결과 개수 제한은 적용하지 않습니다.

| 스크립트 | 응답 원문 | 정리한 응답 |
| --- | --- | --- |
| `llm_based_schema_script.sh` | `llm_based_schema_raw_1.json` | `llm_based_schema_response_1.json` |
| `llm_based_schema_open_ai_script.sh` | `llm_based_schema_open_ai_raw_1.json` | `llm_based_schema_open_ai_response_1.json` |

파일은 `OUTPUT_DIR`에 저장하며 실행마다 해당 파일을 비우고 갱신합니다. 명령 누락, 입력 파일·JSON 오류, 연결·HTTP·응답 JSON·API 오류 또는 잘못된 결과 형식에는 비정상 종료하고 수신 원문을 보존합니다. SQL 생성이나 데이터베이스 실행은 자동으로 수행하지 않습니다.

### Triplet 기반 스키마 검색 호출

다음 스크립트는 각 검색 API를 한 번 호출합니다. 기본 검색에는 서버의 `LLM_API_BASE`, `LLM_MODEL_NAME`과 `<RDF_TRIPLET>/triplets.json`이 필요합니다. OpenAI 검색에는 `OPENAI_API_KEY`, `OPENAI_LLM_MODEL_NAME`과 `<RDF_TRIPLET_OPEN_AI>/triplets.json`이 필요합니다. RDF 파일이 없으면 대응하는 `/retrieve/generate_triplet` 또는 `/retrieve/generate_triplet_open_ai`를 먼저 실행합니다. 검색 스크립트가 관계 생성·SQL 생성을 자동으로 호출하지는 않습니다.

```bash
bash tests/scripts/triplet_based_schema_script.sh
bash tests/scripts/triplet_based_schema_open_ai_script.sh
bash tests/scripts/only_triplet_based_schema_script.sh
bash tests/scripts/only_triplet_based_schema_open_ai_script.sh
```

네 스크립트의 기본 요청은 `tests/test_doc/triplet_based_schema.json`입니다. 기존 임베딩 검색 예시와 동일한 질의·대화 4개와 `max_hop: 3`, `top_k: -1`을 전달합니다. 서버는 최근 메시지 3개와 현재 질의를 검색에 사용합니다. 요청 파일은 스크립트 위치를 기준으로 찾으므로 다른 디렉터리에서도 실행할 수 있습니다.

선택 인자 `[request.json]`으로 별도 요청을 전달할 수 있습니다. `BASE_URL`의 공통 기본값은 `http://localhost:31065`입니다. `OUTPUT_DIR`의 기본값은 `tests/results/`이며 `--help`로 사용법을 확인할 수 있습니다. 사용자 지정 요청은 수정하지 않고 그대로 전달합니다.

요청의 `top_k`는 시작 테이블 수의 상한으로 양의 정수 또는 `-1`(무제한)을 허용합니다. `max_hop`은 확장 깊이로 `0`은 시작 테이블만, 양수는 해당 깊이까지, `-1`은 재방문을 건너뛰며 최대 10-hop까지 탐색합니다. 두 필드를 생략하면 각각 `-1`, `3`을 사용합니다. `only_triplet` 엔드포인트는 시작 테이블을 선택한 뒤 확장된 테이블을 모두 반환합니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR='./triplet search results' \
  bash tests/scripts/triplet_based_schema_script.sh

BASE_URL=http://localhost:2711 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/triplet_based_schema_open_ai_script.sh ./my_request.json

BASE_URL=http://localhost:31065 OUTPUT_DIR=./only_triplet_results \
  bash tests/scripts/only_triplet_based_schema_open_ai_script.sh ./my_request.json
```

스크립트는 요청 시각, HTTP 상태, 요청·전체 처리 시간, 시작·확장·반환 테이블 수, hop 상한과 테이블별 이름·DDL·설명을 출력합니다. 성공 응답의 `table_name`, `schema`, `description`이 같은 길이의 문자열 리스트이며 테이블명이 중복되지 않는지 검사합니다. 메타데이터의 개수와 hop 상한도 확인하며, 양수 `top_k`는 `seed_count`에만 적용합니다. 확장 후 최종 반환 수는 `top_k`보다 많을 수 있습니다. `only_triplet`은 확장 수와 반환 수가 같은지도 확인하며 빈 결과도 성공입니다.

| 스크립트 | 응답 원문 | 정리한 응답 |
| --- | --- | --- |
| `triplet_based_schema_script.sh` | `triplet_based_schema_raw_1.json` | `triplet_based_schema_response_1.json` |
| `triplet_based_schema_open_ai_script.sh` | `triplet_based_schema_open_ai_raw_1.json` | `triplet_based_schema_open_ai_response_1.json` |
| `only_triplet_based_schema_script.sh` | `only_triplet_based_schema_raw_1.json` | `only_triplet_based_schema_response_1.json` |
| `only_triplet_based_schema_open_ai_script.sh` | `only_triplet_based_schema_open_ai_raw_1.json` | `only_triplet_based_schema_open_ai_response_1.json` |

파일은 `OUTPUT_DIR`에 저장하며 실행마다 해당 파일을 비우고 갱신합니다. 명령 누락, 잘못된 입력 JSON, 연결·HTTP·JSON·API 오류 또는 잘못된 응답 형식에는 0이 아닌 종료 코드를 반환하고 수신한 원문 응답을 보존합니다. 스크립트는 API 키를 읽거나 전달하지 않으며 서버 설정을 사용합니다.

### Hybrid 기반 스키마 검색 호출

다음 두 스크립트는 triplet 임베딩으로 시작점을 선택하고 RDF 관계로 확장한 전체 스키마를 검색합니다. 기본 요청은 `tests/test_doc/triplet_based_schema.json`으로 현재 질의·대화 4개와 `top_k: -1`, `max_hop: 3`을 전달합니다. 서버는 최근 대화 3개를 사용합니다.

```bash
BASE_URL=http://localhost:31065 bash tests/scripts/hybrid_only_based_schema_script.sh
bash tests/scripts/hybrid_only_based_schema_open_ai_script.sh
```

일반 검색은 서버의 `EMBEDDING_API_BASE`, `EMBEDDED_TRIPLET`, `RDF_TRIPLET` 설정을 사용하고, 기본 RDF 파일이 없을 때만 `RDF_TRIPLET_OPEN_ROUTER/triplets.json`을 대신 읽습니다. OpenAI 검색은 `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL_NAME`, `EMBEDDED_TRIPLET_OPEN_AI`, `RDF_TRIPLET_OPEN_AI`를 사용하며 입력을 대체하지 않습니다. 각 검색에는 사용할 RDF 파일과 triplet 인덱스가 모두 필요합니다. 일반 버전은 `/retrieve/generate_triplet` 또는 `/retrieve/generate_triplet_open_router`로 관계를 준비하고 `/retrieve/indexing_triplet`으로 인덱싱하며, OpenAI 버전은 `/retrieve/generate_triplet_open_ai`와 `/retrieve/indexing_triplet_open_ai`로 데이터를 준비합니다. 검색 시에는 LLM을 호출하지 않습니다.

두 스크립트는 선택 인자 `[request.json]`, `--help`, `BASE_URL`, `OUTPUT_DIR`을 지원합니다. 공통 기본 서버 주소는 `http://localhost:31065`이고, 기본 결과 디렉터리는 `tests/results/`입니다. 기본 요청은 스크립트 위치 기준으로 찾으며 사용자 지정 JSON은 수정 없이 전달합니다.

시작점과 탐색 깊이를 지정하려면 다음과 같은 JSON 파일을 전달합니다. `top_k`를 생략하면 `-1`, `max_hop`을 생략하면 `3`으로 처리합니다.

```json
{"query":"학과별 과목 정보를 조회해줘","history":[],"top_k":1,"max_hop":2}
```

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR='./hybrid search results' \
  bash tests/scripts/hybrid_only_based_schema_open_ai_script.sh ./my_request.json
```

요청 시각, HTTP 상태, 응답·전체 처리 시간, 임베딩 모델, 미매핑·시작·확장·반환 테이블 수, hop 상한과 테이블별 이름·DDL·설명을 출력합니다. 성공 응답의 동일 길이 문자열 리스트와 테이블명 중복, 메타데이터의 모델명·개수·요청 `top_k` 일치 여부를 검사합니다. 양수 `top_k`는 시작 테이블 수만 제한하므로 확장된 반환 수는 더 많을 수 있습니다. `expanded_count`와 `retrieved_count`는 같아야 하고 반환 리스트 길이와도 일치해야 합니다. `max_hop=-1`이면 응답의 탐색 상한 `graph_depth`는 `10`이어야 합니다. 빈 결과도 성공입니다.

| 스크립트 | 응답 원문 | 정리한 응답 |
| --- | --- | --- |
| `hybrid_only_based_schema_script.sh` | `hybrid_only_based_schema_raw_1.json` | `hybrid_only_based_schema_response_1.json` |
| `hybrid_only_based_schema_open_ai_script.sh` | `hybrid_only_based_schema_open_ai_raw_1.json` | `hybrid_only_based_schema_open_ai_response_1.json` |

요청 전에 해당 응답 파일을 비웁니다. 잘못된 입력, 연결·HTTP·JSON·API 오류 또는 잘못된 응답에는 0이 아닌 종료 코드를 반환하고 수신 원문을 보존합니다. 스크립트는 API 키를 읽거나 전달하지 않으며 서버 설정을 사용합니다.

### 히스토리와 원본 스키마를 이용한 SQL 생성 호출

서버를 실행하고 `LLM_API_BASE`, `LLM_MODEL_NAME`을 설정한 뒤, `text_to_sql_api` 디렉터리에서 다음 스크립트를 실행합니다. 기본 요청에 원본 스키마가 포함되어 있어 인덱싱·검색을 먼저 실행할 필요는 없습니다.

```bash
bash tests/scripts/llm_based_generate_sql_script.sh
```

기본 요청 파일은 `tests/test_doc/llm_based_generate_sql.json`입니다. `dataset/db_schema.json`의 `SUBJECT_SUMMARY` 원본 DDL과 설명을 그대로 포함합니다. 대화 메시지 4개로 학과별 집계에서 학기별 집계로 조건을 구체화하고, 현재 질의에서 학기 코드 오름차순과 같은 학기 내 수강 인원 합계 내림차순 정렬을 요청합니다. 서버는 최근 3개 메시지와 현재 질의를 사용합니다.

실제 컬럼인 `TERM_CODE`, `DEPARTMENT_CODE`, `DEPARTMENT_NAME`을 기준으로 `SUBJECT_ENROLLMENT_NUMBER`를 합산할 수 있는 예시입니다. 집계 대상은 교과목 수강 인원의 합계이며, 중복을 제거한 학생 수를 뜻하지 않습니다. 생성 문법은 PostgreSQL입니다.

기본 요청 파일은 스크립트 위치를 기준으로 찾습니다. 선택 인자 `[request.json]`으로 다른 요청을 전달하거나, `BASE_URL`과 `OUTPUT_DIR`로 서버·출력 경로를 변경할 수 있습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR=./sql_results \
  bash tests/scripts/llm_based_generate_sql_script.sh

bash tests/scripts/llm_based_generate_sql_script.sh ./my_sql_request.json
```

스크립트는 요청을 한 번 POST하고 요청 시각, HTTP 상태, 응답 시간과 전체 처리 시간을 출력합니다. 성공 상태와 `data`의 비어 있지 않은 SQL 문자열을 확인한 뒤 콘솔에 SQL을 출력하고 다음 파일을 저장합니다.

- `llm_based_generate_sql_raw_1.json`: 응답 원문
- `llm_based_generate_sql_response_1.json`: 정리한 JSON 응답
- `llm_based_generate_sql_1.sql`: `data`의 SQL 문자열

기본 저장 위치는 `tests/results/`입니다. 요청 전에 같은 이름의 이전 결과 파일을 비우므로 실패한 요청에 이전 SQL이 남지 않습니다. 연결·HTTP·JSON·API 오류와 잘못된 `data`에는 0이 아닌 종료 코드를 반환하며, 받은 원문을 보존합니다. SQL을 실행하거나 특정 SQL 문자열과 일치하는지 검사하지 않습니다. `--help`로 사용법을 확인할 수 있습니다.

### 테이블 관계 Triplet 생성 호출

서버의 `LLM_API_BASE`, `LLM_MODEL_NAME`을 설정하고 서버를 실행한 뒤, `text_to_sql_api` 디렉터리에서 다음 스크립트를 실행합니다. 요청 본문이나 입력 JSON 인자는 필요하지 않습니다. 서버가 설정된 데이터셋 전체를 읽고 실제 LLM으로 관계를 생성하며, 성공 시 서버의 `<RDF_TRIPLET>/triplets.json`을 갱신합니다. 인덱싱·검색을 먼저 실행할 필요는 없습니다.

```bash
bash tests/scripts/generate_triplet_script.sh
```

`BASE_URL`(기본 `http://localhost:31065`)과 `OUTPUT_DIR`(기본 `tests/results/`)로 서버 주소와 응답 저장 경로를 변경할 수 있습니다. Bash, `curl`, `jq`, `date`, `awk`, `mkdir`, `dirname`이 필요하며, `--help`로 사용법을 확인할 수 있습니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR=./triplet_results \
  bash tests/scripts/generate_triplet_script.sh
```

스크립트는 `/retrieve/generate_triplet`에 한 번 POST하고 요청 시각, HTTP 상태, 응답 시간과 전체 처리 시간을 출력합니다. 성공 상태와 `data`의 각 항목이 `[주체 객체, 관계 문자열, 대상 객체]` 형식인지 확인합니다. 주체·대상 객체의 `table_name`, `description`과 관계는 공백뿐이지 않은 문자열이어야 합니다. 관계가 없는 빈 목록도 성공으로 처리하며, 콘솔에 관계 수와 각 주체·관계·대상을 출력합니다.

다음 파일을 `tests/results/` 또는 지정한 `OUTPUT_DIR`에 저장합니다. `generate_triplet_1.json`은 클라이언트에 저장하는 응답 데이터이며, 서버의 저장 경로와 구분됩니다.

- `generate_triplet_raw_1.json`: 응답 원문
- `generate_triplet_response_1.json`: 정리한 전체 JSON 응답
- `generate_triplet_1.json`: 검증된 `data` 목록

요청 전에 같은 이름의 이전 결과 파일을 비웁니다. 연결·HTTP·JSON·API 오류와 잘못된 Triplet 형식에는 0이 아닌 종료 코드를 반환하며, 받은 원문을 보존합니다. 검증에 실패하면 Triplet 데이터 파일은 빈 상태로 남습니다.

### OpenAI 엔드포인트별 테스트 스크립트

서버 `.env`에 실제 `OPENAI_API_KEY`를 설정하고 서버를 시작한 뒤, `text_to_sql_api` 디렉터리에서 각 스크립트를 실행합니다. 스크립트는 애플리케이션 서버만 호출하며 OpenAI 키는 서버가 읽습니다.

```bash
bash tests/scripts/indexing_schema_open_ai_script.sh
bash tests/scripts/indexing_triplet_open_ai_script.sh
bash tests/scripts/embedding_based_schema_open_ai_script.sh
bash tests/scripts/embedded_triplet_based_schema_open_ai_script.sh
bash tests/scripts/llm_based_schema_open_ai_script.sh
bash tests/scripts/triplet_based_schema_open_ai_script.sh
bash tests/scripts/only_triplet_based_schema_open_ai_script.sh
bash tests/scripts/hybrid_only_based_schema_open_ai_script.sh
bash tests/scripts/llm_based_generate_sql_open_ai_script.sh
bash tests/scripts/generate_triplet_open_ai_script.sh
```

각 스크립트는 해당 엔드포인트에 한 번 POST하며 독립적으로 실행합니다. OpenAI 스키마 임베딩 검색 전에는 OpenAI 스키마 인덱싱을 완료해야 하고, triplet 임베딩 검색 전에는 OpenAI triplet 인덱싱을 완료해야 합니다. OpenAI triplet 인덱싱은 `<RDF_TRIPLET_OPEN_AI>/triplets.json`이 필요합니다. OpenAI hybrid 검색에는 OpenAI triplet 파일과 인덱스가 모두 필요합니다. OpenAI LLM 기반 검색은 원본 카탈로그만 있으면 실행할 수 있습니다. `/retrieve/triplet_based_schema_open_ai`와 `/retrieve/only_triplet_based_schema_open_ai` 검색 전에는 OpenAI triplet 파일을 준비해야 하며 인덱싱은 필요하지 않습니다. SQL 생성용 기본 요청에는 원본 스키마가 포함되어 있으므로 SQL 생성 스크립트는 개별 실행할 수 있습니다.

| 스크립트 | 호출 엔드포인트 | 기본 요청 |
| --- | --- | --- |
| `indexing_schema_open_ai_script.sh` | `/retrieve/indexing_schema_open_ai` | 본문 없음 |
| `indexing_triplet_open_ai_script.sh` | `/retrieve/indexing_triplet_open_ai` | 본문 없음 |
| `embedding_based_schema_open_ai_script.sh` | `/retrieve/embedding_based_schema_open_ai` | `tests/test_doc/embedding_based_schema.json` |
| `embedded_triplet_based_schema_open_ai_script.sh` | `/retrieve/embedded_triplet_based_schema_open_ai` | `tests/test_doc/embedding_based_schema.json` |
| `llm_based_schema_open_ai_script.sh` | `/retrieve/llm_based_schema_open_ai` | `tests/test_doc/llm_based_schema.json` |
| `triplet_based_schema_open_ai_script.sh` | `/retrieve/triplet_based_schema_open_ai` | `tests/test_doc/triplet_based_schema.json` |
| `only_triplet_based_schema_open_ai_script.sh` | `/retrieve/only_triplet_based_schema_open_ai` | `tests/test_doc/triplet_based_schema.json` |
| `hybrid_only_based_schema_open_ai_script.sh` | `/retrieve/hybrid_only_based_schema_open_ai` | `tests/test_doc/triplet_based_schema.json` |
| `llm_based_generate_sql_open_ai_script.sh` | `/generate_sql/llm_based_generate_sql_open_ai` | `tests/test_doc/llm_based_generate_sql.json` |
| `generate_triplet_open_ai_script.sh` | `/retrieve/generate_triplet_open_ai` | 본문 없음 |

검색·SQL 생성의 기본 요청은 위에서 설명한 데이터셋 기반 예시를 재사용합니다. 검색은 학기·학과별 과목과 학점·수강 인원 정보를 찾으며, 임베딩 검색은 `top_k=3`, triplet·hybrid 검색은 `top_k=-1`과 `max_hop=3`을 전달합니다. LLM 검색에는 `top_k`를 전달하지 않습니다. SQL 생성은 `SUBJECT_SUMMARY` 원본 DDL·설명을 이용해 학기·학과별 교과목 수강 인원을 합산하고 정렬하도록 요청합니다. 각 요청은 대화 메시지 4개를 전달하고 서버는 최근 3개를 사용합니다.

모든 스크립트는 `BASE_URL`, `OUTPUT_DIR`(기본 `tests/results/`), `--help`를 지원합니다. 검색·SQL 생성 스크립트는 선택 인자 `[request.json]`을 지원하며 기본 파일은 스크립트 위치 기준으로 찾습니다. OpenAI Triplet 검색·hybrid 검색·임베딩 검색·인덱싱 스크립트의 기본 주소는 `http://localhost:31065`입니다. 호스트에서 호출할 때는 공개 포트에 맞춰 `BASE_URL`을 지정하고 각 스크립트의 기본 주소를 확인합니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/indexing_schema_open_ai_script.sh

BASE_URL=http://localhost:8001 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/embedding_based_schema_open_ai_script.sh ./my_search_request.json

BASE_URL=http://localhost:8001 OUTPUT_DIR=./open_ai_results \
  bash tests/scripts/llm_based_generate_sql_open_ai_script.sh ./my_sql_request.json

BASE_URL=http://localhost:2711 \
  bash tests/scripts/generate_triplet_open_ai_script.sh
```

출력 파일명은 엔드포인트별로 구분합니다.

| 테스트 | 응답 원문 | 정리한 응답 | 추출 결과 파일 |
| --- | --- | --- | --- |
| 인덱싱 | `indexing_schema_open_ai_raw_1.json` | `indexing_schema_open_ai_response_1.json` | 없음 |
| Triplet 인덱싱 | `indexing_triplet_open_ai_raw_1.json` | `indexing_triplet_open_ai_response_1.json` | 없음 |
| 검색 | `embedding_based_schema_open_ai_raw_1.json` | `embedding_based_schema_open_ai_response_1.json` | 없음 |
| Triplet 임베딩 검색 | `embedded_triplet_based_schema_open_ai_raw_1.json` | `embedded_triplet_based_schema_open_ai_response_1.json` | 없음 |
| LLM 검색 | `llm_based_schema_open_ai_raw_1.json` | `llm_based_schema_open_ai_response_1.json` | 없음 |
| Triplet 검색 | `triplet_based_schema_open_ai_raw_1.json` | `triplet_based_schema_open_ai_response_1.json` | 없음 |
| Only-triplet 검색 | `only_triplet_based_schema_open_ai_raw_1.json` | `only_triplet_based_schema_open_ai_response_1.json` | 없음 |
| Hybrid 검색 | `hybrid_only_based_schema_open_ai_raw_1.json` | `hybrid_only_based_schema_open_ai_response_1.json` | 없음 |
| SQL 생성 | `llm_based_generate_sql_open_ai_raw_1.json` | `llm_based_generate_sql_open_ai_response_1.json` | `llm_based_generate_sql_open_ai_1.sql` |
| Triplet 생성 | `generate_triplet_open_ai_raw_1.json` | `generate_triplet_open_ai_response_1.json` | `generate_triplet_open_ai_1.json` |

Triplet 생성 스크립트는 관계 목록의 형식을 검증하고 관계 수와 각 주체·관계·대상을 출력합니다. 빈 목록도 성공으로 처리하며, 클라이언트 결과 파일은 `tests/results/`에, 서버의 원본 저장 파일은 `RDF_TRIPLET_OPEN_AI`에 각각 저장합니다.

요청 시각, HTTP 상태, 응답·전체 처리 시간을 출력합니다. 인덱싱은 추가·건너뜀·전체 개수를, 검색은 반환 순서대로 테이블 이름·DDL·설명을, SQL 생성은 반환된 SQL을 출력합니다. 검색 결과의 세 문자열 리스트 길이를 검사합니다. 임베딩 검색은 최종 반환 수를, triplet·hybrid 검색은 시작 테이블 수를 `top_k`와 비교하며 `-1`이면 시작 테이블 수를 제한하지 않습니다. SQL은 공백뿐이지 않은 문자열인지 확인합니다.

요청 전에 해당 결과 파일을 비웁니다. 연결·HTTP·JSON·API 오류나 잘못된 검색·SQL 결과에는 0이 아닌 종료 코드를 반환하며 받은 원문을 보존합니다. 생성 SQL은 저장·출력하며 실행하지 않습니다.

### OpenRouter 엔드포인트별 테스트 스크립트

서버 `.env`에 `OPENROUTER_API_KEY`, `OPENROUTER_LLM_MODEL_NAME`, `RDF_TRIPLET_OPEN_ROUTER`를 설정하고 서버를 시작한 뒤 다음 스크립트를 각각 실행합니다. 스크립트는 애플리케이션 서버만 호출하며 OpenRouter 키는 서버가 읽습니다.

```bash
bash tests/scripts/generate_triplet_open_router_script.sh
bash tests/scripts/llm_based_generate_sql_open_router_script.sh
bash tests/scripts/llm_based_schema_open_router_script.sh
bash tests/scripts/only_triplet_based_schema_open_router_script.sh
bash tests/scripts/triplet_based_schema_open_router_script.sh
```

각 스크립트는 대응하는 API를 한 번 POST합니다. Triplet 생성은 전체 카탈로그에서 관계를 생성하여 서버의 `<RDF_TRIPLET_OPEN_ROUTER>/triplets.json`을 갱신하며 요청 본문은 없습니다. Triplet·only-triplet 검색에는 이 RDF 파일이 필요하므로 먼저 생성 API로 준비합니다. LLM 스키마 검색은 카탈로그를 사용하며, SQL 생성의 기본 요청에는 원본 스키마가 포함되어 있어 독립적으로 호출할 수 있습니다.

| 스크립트 | 호출 엔드포인트 | 기본 요청 |
| --- | --- | --- |
| `generate_triplet_open_router_script.sh` | `/retrieve/generate_triplet_open_router` | 본문 없음 |
| `llm_based_generate_sql_open_router_script.sh` | `/generate_sql/llm_based_generate_sql_open_router` | `tests/test_doc/llm_based_generate_sql.json` |
| `llm_based_schema_open_router_script.sh` | `/retrieve/llm_based_schema_open_router` | `tests/test_doc/llm_based_schema.json` |
| `only_triplet_based_schema_open_router_script.sh` | `/retrieve/only_triplet_based_schema_open_router` | `tests/test_doc/triplet_based_schema.json` |
| `triplet_based_schema_open_router_script.sh` | `/retrieve/triplet_based_schema_open_router` | `tests/test_doc/triplet_based_schema.json` |

다섯 스크립트 모두 `BASE_URL`(기본 `http://localhost:31065`), `OUTPUT_DIR`(기본 `tests/results/`), `--help`를 지원합니다. SQL 생성·스키마 검색은 선택 인자 `[request.json]`으로 별도 요청을 전달할 수 있습니다. 기본 요청은 스크립트 위치 기준으로 찾고 사용자 지정 JSON은 수정 없이 전달합니다.

```bash
BASE_URL=http://localhost:8001 OUTPUT_DIR='./open router results' \
  bash tests/scripts/generate_triplet_open_router_script.sh

BASE_URL=http://localhost:8001 OUTPUT_DIR='./open router results' \
  bash tests/scripts/only_triplet_based_schema_open_router_script.sh ./my_request.json
```

SQL 생성·검색의 기본 요청은 기존 예시의 질의와 대화 4개를 전달하고 서버는 최근 대화 3개를 사용합니다. Triplet·only-triplet 기본 요청은 `top_k=-1`, `max_hop=3`입니다. 양수 `top_k`는 시작 테이블 수에만 적용하며 최종 반환 수는 더 많을 수 있습니다. 스크립트는 시작·확장·반환 수와 리스트 길이를 검증하고, `max_hop=-1`이면 탐색 상한 `graph_depth=10`을 검사합니다. Only-triplet은 확장 수와 반환 수가 같아야 하며, triplet은 최종 재선별로 반환 수가 더 적을 수 있습니다. LLM 스키마 검색은 결과의 세 문자열 리스트 길이를 검사하고 개수 제한을 적용하지 않습니다. 세 검색은 빈 결과도 성공으로 처리합니다.

요청 시각, HTTP 상태, 응답·전체 처리 시간을 출력하며 검색은 테이블별 이름·DDL·설명을, SQL 생성은 SQL을, triplet 생성은 관계 수와 주체·관계·대상을 출력합니다. SQL 응답은 공백뿐이지 않은 문자열이어야 하며, triplet 응답은 `[주체 객체, 관계 문자열, 대상 객체]` 목록의 형식을 검증합니다.

| 테스트 | 응답 원문 | 정리한 응답 | 추출 결과 파일 |
| --- | --- | --- | --- |
| Triplet 생성 | `generate_triplet_open_router_raw_1.json` | `generate_triplet_open_router_response_1.json` | `generate_triplet_open_router_1.json` |
| SQL 생성 | `llm_based_generate_sql_open_router_raw_1.json` | `llm_based_generate_sql_open_router_response_1.json` | `llm_based_generate_sql_open_router_1.sql` |
| LLM 스키마 검색 | `llm_based_schema_open_router_raw_1.json` | `llm_based_schema_open_router_response_1.json` | 없음 |
| Only-triplet 검색 | `only_triplet_based_schema_open_router_raw_1.json` | `only_triplet_based_schema_open_router_response_1.json` | 없음 |
| Triplet 검색 | `triplet_based_schema_open_router_raw_1.json` | `triplet_based_schema_open_router_response_1.json` | 없음 |

위 파일은 클라이언트의 `OUTPUT_DIR`에 저장합니다. Triplet 생성의 추출 파일은 서버의 RDF 저장 파일과 별개입니다. 요청 전에 해당 결과 파일을 비우며 연결·HTTP·JSON·API 오류나 잘못된 응답에는 0이 아닌 종료 코드를 반환하고 수신 원문을 보존합니다. SQL은 저장·출력하고 실행하지 않습니다.

## 스키마 검색 성능 평가

`evaluation/src/evaluate_schema_retrieval.py`는 `evaluation/dataset/test_set.json`의 각 `question`을 검색 API에 입력하고 `tables`를 정답으로 평가합니다. 현재 데이터셋은 5,787개 질문입니다. 코드 상단의 다음 변수를 수정한 뒤 실행합니다.

```python
ENDPOINT = "/retrieve/embedding_based_schema_open_ai"
BASE_URL = "http://localhost:31065"
TOP_K = 5
MAX_HOP = 3
TIMEOUT_SECONDS = 300.0
RESUME = True
```

| `ENDPOINT`에 지정할 값 | 요청 |
| --- | --- |
| `/retrieve/embedding_based_schema` | `query`, `history: []`, `top_k: TOP_K` |
| `/retrieve/embedded_triplet_based_schema` | `query`, `history: []`, `top_k: TOP_K` |
| `/retrieve/llm_based_schema` | `query`, `history: []` |
| `/retrieve/triplet_based_schema` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/only_triplet_based_schema` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/hybrid_only_based_schema` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/embedding_based_schema_open_ai` | `query`, `history: []`, `top_k: TOP_K` |
| `/retrieve/embedded_triplet_based_schema_open_ai` | `query`, `history: []`, `top_k: TOP_K` |
| `/retrieve/llm_based_schema_open_ai` | `query`, `history: []` |
| `/retrieve/triplet_based_schema_open_ai` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/only_triplet_based_schema_open_ai` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/hybrid_only_based_schema_open_ai` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/llm_based_schema_open_router` | `query`, `history: []` |
| `/retrieve/only_triplet_based_schema_open_router` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |
| `/retrieve/triplet_based_schema_open_router` | `query`, `history: []`, `top_k: TOP_K`, `max_hop: MAX_HOP` |

일반·OpenAI·OpenRouter 검색 엔드포인트 총 15개를 지원합니다. `TOP_K`는 임베딩 검색 4개와 triplet·hybrid 검색 8개의 `top_k` 요청 필드에 전달합니다. 임베딩 검색에서는 최종 반환 테이블 수이며 양의 정수만 허용합니다. Triplet·hybrid 검색에서는 시작 테이블 수의 상한이며 양의 정수 또는 `-1`(무제한)을 허용합니다. 확장 후 반환 테이블 수는 `TOP_K`보다 많을 수 있으며 전체 결과를 평가합니다. `0`, 잘못된 음수·자료형은 API 호출·결과 저장 전에 거부합니다. LLM 검색 3개에는 `top_k`를 전달하지 않습니다.

`MAX_HOP`은 위 표의 triplet·only-triplet·hybrid 검색 8개의 `max_hop` 요청 필드에 전달합니다. 코드 상단에 설정한 값을 사용하며 `0`은 시작 테이블만, 양수는 지정한 깊이까지 탐색합니다. `-1`은 API에 그대로 전달하여 재방문 테이블을 건너뛰며 최대 10-hop까지 탐색합니다. 이 8개 엔드포인트에서는 `-1` 또는 0 이상의 정수만 허용하며 잘못된 값은 API 호출·결과 저장 전에 거부합니다. 나머지 7개 엔드포인트에서는 이 설정을 사용하지 않습니다.

질문은 원문 그대로 전달하며 정답 `tables`, SQL 등 다른 데이터셋 필드는 요청에 포함하지 않습니다. 데이터셋 순서대로 질문마다 한 번씩 순차 호출하고, 한 실행 안에서 실패한 요청을 자동으로 재시도하지 않습니다. 연결 제한 시간은 최대 10초, 나머지 HTTP 작업의 제한 시간은 `TIMEOUT_SECONDS`입니다.

평가할 API 서버를 먼저 실행하고 해당 엔드포인트의 인덱스·카탈로그·RDF와 모델 설정을 준비합니다. 일반 검색은 서버의 `EMBEDDING_API_BASE` 또는 `LLM_API_BASE` 등 대응 설정을 사용합니다. OpenAI 검색은 서버의 OpenAI 모델·키 설정을 사용합니다. OpenRouter 검색은 서버의 `OPENROUTER_API_KEY`, `OPENROUTER_LLM_MODEL_NAME`을 사용하며, triplet·only-triplet 검색에는 `RDF_TRIPLET_OPEN_ROUTER/triplets.json`도 필요합니다. 평가 코드는 실행 중인 API 서버를 호출하며 API 키는 서버가 읽습니다. 필요한 Python 의존성은 기존 `requirements.txt`의 `httpx`입니다.

```bash
python3 evaluation/src/evaluate_schema_retrieval.py
```

기본 데이터셋과 결과 경로는 평가 코드 위치에서 계산하므로 다른 작업 디렉터리에서 절대 경로로 실행해도 같습니다. 결과는 `evaluation/result/` 아래에 실제 요청에 사용하는 설정값을 구분하여 저장합니다. `endpoint_name`은 엔드포인트 경로의 마지막 이름입니다.

| 검색 계열 | 파일명 규칙 | 예시 |
| --- | --- | --- |
| Triplet·only-triplet·hybrid 검색 8개 | `{endpoint_name}_{TOP_K}_{MAX_HOP}.json` | `only_triplet_based_schema_open_ai_1_2.json` |
| 스키마·triplet 임베딩 검색 4개 | `{endpoint_name}_{TOP_K}.json` | `embedding_based_schema_open_ai_5.json` |
| LLM 검색 3개 | `{endpoint_name}.json` | `llm_based_schema_open_router.json` |

예를 들어 `ENDPOINT="/retrieve/only_triplet_based_schema_open_ai"`, `TOP_K=1`, `MAX_HOP=2`이면 `evaluation/result/only_triplet_based_schema_open_ai_1_2.json`에 저장합니다. `-1`도 입력값 그대로 붙이므로 시작점 무제한·최대 10-hop 설정은 `only_triplet_based_schema_open_ai_-1_-1.json`이 됩니다. 임베딩 검색은 `MAX_HOP`을 요청에 사용하지 않으므로 파일명에도 포함하지 않습니다.

### 평가 지표와 실패 처리

정답 `tables`와 응답의 `data.table_name`에서 중복을 제거하고 테이블명 집합으로 비교합니다. 원래 이름의 대소문자·공백을 그대로 비교하며 순서는 점수에 영향을 주지 않습니다. 저장하는 테이블 리스트에는 처음 등장한 순서를 유지합니다. 정답 또는 검색 결과에 같은 이름이 여러 번 등장해도 한 개로 계산합니다.

| 지표 | 정의 |
| --- | --- |
| TP | 정답과 검색 결과 양쪽에 있는 고유 테이블 수 |
| FP | 검색됐지만 정답에 없는 고유 테이블 수 |
| FN | 정답에 있지만 검색되지 않은 고유 테이블 수 |
| Precision | `TP / (TP + FP)` |
| Recall | `TP / (TP + FN)` |
| F1 | `2 × TP / (2 × TP + FP + FN)` |
| Exact match | 정답 집합과 검색 결과 집합이 완전히 같은지 여부 |

분모가 0인 Precision·Recall·F1은 0입니다. 정답과 검색 결과가 모두 비어 있으면 정확일치는 참이고 세 지표는 0입니다. `macro`는 성공한 질문별 지표의 산술 평균이며 `micro`는 성공 항목의 TP·FP·FN을 합산한 뒤 계산합니다. `exact_match_rate`는 성공 항목 중 정확일치 비율입니다.

HTTP 오류, API의 오류 상태, 타임아웃·연결 실패와 잘못된 JSON·응답 구조는 실패로 기록하고 다음 질문을 계속 평가합니다. 유효한 빈 검색 결과는 성공으로 채점합니다. 실패 항목의 `predicted_tables`, `metrics`는 `null`이며 검색 지표 집계에서 제외합니다. 모든 항목이 실패하면 검색 지표는 `null`입니다.

`success_rate`는 처리된 항목 중 성공한 비율이며, 미처리 항목은 분모에 포함하지 않습니다. `mean_latency_seconds`는 성공 항목의 클라이언트 측 처리 시간 평균이고, `mean_api_elapsed_time`은 유효한 서버 `elapsed_time`을 받은 성공 항목의 평균입니다. 실패 항목도 개별 처리 시간을 저장합니다.

### 결과 구조와 이어서 실행

결과 JSON에는 다음 내용을 저장합니다. DDL·설명 본문은 저장하지 않습니다.

| 필드 | 내용 |
| --- | --- |
| `format_version` | 결과 파일 형식 버전 |
| `configuration` | 엔드포인트, 서버 주소, 유효한 top-k·max_hop, 제한 시간, 데이터셋 절대 경로와 SHA-256. `top_k`는 임베딩·triplet·hybrid 검색에 실제 전달한 값이며 LLM 검색에서는 `null`. `max_hop`은 triplet·hybrid 검색에 입력한 값이며 나머지는 `null` |
| `started_at`, `updated_at` | 이번 실행의 시작·마지막 저장 UTC 시각 |
| `state` | `running`, `interrupted`, `complete`. `complete`는 전체 항목의 처리가 끝났다는 뜻이며 실패가 포함될 수 있습니다. |
| `summary` | 전체·처리·성공·실패·미처리 건수, 성공률, macro·micro 지표, 정확일치율, 평균 시간 |
| `results` | 데이터셋 순서로 정렬된 질문별 결과 |

각 결과에는 `index`, `id`, `question`, `expected_tables`, `predicted_tables`, `status`, `http_status`, `metrics`, `latency_seconds`, `api_elapsed_time`, `completed_at`, `error`를 담습니다. `metrics`에는 TP·FP·FN, Precision·Recall·F1과 `exact_match`를 저장하고, 실패의 `error`에는 오류 종류와 메시지를 저장합니다. `index`는 데이터셋에서의 0부터 시작하는 위치이며 `id`가 없는 입력은 위치를 ID로 사용합니다.

매 항목 처리 후 임시 파일을 작성하고 원자적으로 교체합니다. 실행 중에는 진행 상황을, 종료 시에는 집계와 결과 저장 위치를 출력합니다. `Ctrl+C`로 중단하면 완료한 결과를 저장하고 종료 코드 130을 반환합니다. 모든 항목의 평가가 끝나면 성공만 있을 때 종료 코드 0, 실패가 남아 있으면 1을 반환합니다. 설정·데이터셋·저장 오류도 종료 코드 1입니다.

`RESUME=True`가 기본값입니다. 같은 설정의 기존 결과가 있으면 성공 항목은 재사용하고 실패·미처리 항목만 다시 호출합니다. 재시도한 항목은 기존 항목을 갱신하고 지표를 다시 집계합니다. 엔드포인트·서버 주소·유효한 top-k·max_hop·데이터셋 SHA-256과 파일 형식이 일치하는 결과만 재사용합니다. 데이터셋 SHA-256은 파일 전체 바이트 기준이므로 내용이나 공백을 바꿔도 다른 데이터셋으로 판단합니다.

평가는 현재 설정으로 생성한 파일명에서만 재개합니다. `TOP_K`를 바꾸거나 triplet·hybrid 검색의 `MAX_HOP`을 바꾸면 별도 파일에서 평가하여 기존 설정의 결과를 보존합니다. 같은 설정으로 돌아오면 해당 파일에서 이어서 실행할 수 있습니다. 파일 내부의 `configuration.top_k`·`configuration.max_hop`이 파일명에 대응하는 요청 설정과 다르거나 잘못된 값이면 기존 파일을 보존하고 재개를 거부합니다. `RESUME=False`는 현재 선택한 설정의 파일만 전체 재평가하여 갱신합니다. LLM 검색은 `TOP_K`·`MAX_HOP`을 바꿔도 같은 파일을 사용합니다.

이전 triplet 평가 결과의 `configuration.top_k: null`은 `top_k`를 전송하지 않아 시작 테이블 수가 무제한이었던 결과입니다. 이 내용을 현재 이름의 파일로 직접 옮겨 사용한다면 기존처럼 `-1`로 해석하여 `TOP_K=-1`일 때만 재개합니다. 양수 설정의 파일에 무제한 결과를 넣으면 재개를 거부합니다.

이전 이름의 임베딩 결과 `{endpoint_name}.json`과 triplet·hybrid 결과 `{endpoint_name}.json`, `{endpoint_name}_{MAX_HOP}.json`은 그대로 보존하며 새 이름으로 자동 이동하거나 재개하지 않습니다. 새 이름의 파일이 없으면 새로운 평가를 시작합니다. LLM 검색의 파일명은 유지됩니다. Triplet·hybrid 외 엔드포인트는 선택한 파일의 `configuration.max_hop` 기록이 없는 이전 JSON 형식도 재개할 수 있습니다.

결과가 손상되었거나 설정·데이터셋이 다르면 기존 결과를 보존하고 실행을 중단합니다. 새 평가가 필요하면 코드 상단을 `RESUME=False`로 변경하여 전체 질문을 다시 호출하고 파일을 갱신합니다. 서버 측 모델·인덱스·RDF 변경 여부는 자동으로 판단하지 않으므로 이러한 데이터를 변경한 뒤에도 `RESUME=False`로 재평가합니다. 같은 결과 파일을 사용하는 평가는 한 프로세스씩 실행합니다.

```bash
python3 -m pytest tests/test_schema_retrieval_evaluation.py -q
```

테스트는 모의 HTTP로 15개 엔드포인트의 요청 형식, 지표 계산, 오류 분리, 공급자·엔드포인트별 결과 저장과 재개를 검증합니다. 실제 API 서버나 모델 API는 호출하지 않습니다.

## 예측 스키마 기반 SQL 생성 성능 평가

`evaluation/src/evaluate_sql_generation.py`는 각 항목의 `question`과 `predicted_schema`로 SQL 생성 API를 호출하고, `sql`을 정답으로 비교합니다. 평가용 의존성을 설치한 뒤 코드 상단 변수를 설정합니다.

```bash
python3 -m pip install -r requirements-dev.txt
```

```python
ENDPOINT = "/generate_sql/llm_based_generate_sql_open_ai"
DATASET_PATH = "evaluation/dataset/embedding_based_schema_test_set.json"
BASE_URL = "http://localhost:31065"
TIMEOUT_SECONDS = 300.0
RESUME = True
GOLD_SQL_DIALECT = "oracle"
GENERATED_SQL_DIALECT = "postgres"
```

`ENDPOINT`는 `/generate_sql/llm_based_generate_sql_open_ai` 또는 `/generate_sql/llm_based_generate_sql` 중 하나입니다. OpenAI·일반 모델과 인증 설정은 API 서버가 읽습니다. 평가 코드에서 API 키를 읽거나 전달하지 않습니다.

`DATASET_PATH`에는 같은 구조의 기존 JSON 파일을 지정합니다. 현재 준비된 예측 스키마 테스트셋은 다음과 같습니다.

- `evaluation/dataset/embedding_based_schema_test_set.json`
- `evaluation/dataset/embedding_based_schema_open_ai_schema_test_set.json`
- `evaluation/dataset/embedded_triplet_based_schema_open_ai_test_set.json`

LLM·triplet 검색 기반 데이터셋도 각 항목에 공백뿐이지 않은 `question`, 정답 `sql`, 비어 있지 않은 DDL 문자열 리스트 `predicted_schema`를 준비하면 같은 코드로 평가할 수 있습니다. `gold_schema_test_set.json`은 아래 정답 스키마 평가 코드인 `evaluate_gold_schema_sql_generation.py`로 실행합니다.

### SQL 생성 요청과 실행

`predicted_schema`의 각 DDL을 `dataset/db_schema.json`의 `schema`와 정확히 매핑하여 테이블명·DDL·카탈로그 설명 리스트를 구성합니다. 이 카탈로그 경로는 코드의 `CATALOG_PATH`로 설정합니다. 입력의 DDL 순서와 중복을 그대로 유지합니다. 카탈로그의 테이블명과 DDL은 각각 유일해야 합니다. 미등록·모호한 DDL, 빈 예측 스키마 또는 잘못된 데이터셋은 API 호출 전에 오류로 종료합니다.

API 요청은 다음 세 필드만 포함합니다.

```text
query   ← question 원문
history ← []
schema  ← predicted_schema에 대응하는 table_name, schema, description 리스트
```

정답 `sql`, 정답 `tables`, 정답 `schema`와 데이터셋의 다른 필드는 요청에 포함하지 않습니다. 평가할 API 서버와 모델 설정을 준비한 뒤 실행합니다.

```bash
python3 evaluation/src/evaluate_sql_generation.py
```

상대 경로는 프로젝트 루트 기준으로 해석하므로 다른 작업 디렉터리에서 절대 경로로 실행해도 기본 입력·출력 위치는 같습니다. 질문을 순서대로 한 번씩 호출하며 같은 실행 안에서 자동 재시도하지 않습니다. 연결 제한 시간은 최대 10초, 나머지 HTTP 작업의 제한 시간은 `TIMEOUT_SECONDS`입니다.

결과 파일은 선택한 데이터셋 파일명에서 마지막 확장자를 제외한 이름을 사용하여 `evaluation/result/{dataset_name}.json`에 저장합니다. 두 SQL 생성 엔드포인트에 같은 규칙을 적용합니다. `result_dir`를 지정하면 그 디렉터리에 같은 파일명으로 저장합니다.

| 입력 데이터셋 | 결과 파일 |
| --- | --- |
| `embedding_based_schema_test_set.json` | `evaluation/result/embedding_based_schema_test_set.json` |
| `embedding_based_schema_open_ai_schema_test_set.json` | `evaluation/result/embedding_based_schema_open_ai_schema_test_set.json` |
| `embedded_triplet_based_schema_open_ai_test_set.json` | `evaluation/result/embedded_triplet_based_schema_open_ai_test_set.json` |

기존 엔드포인트명 결과 파일은 자동으로 이동하거나 삭제하지 않습니다.

### SQL 평가 지표

정답 SQL은 기본 Oracle 방언으로, 생성 SQL은 API가 지정한 PostgreSQL 방언으로 파싱합니다. SQLGlot `30.18.0`으로 단일 조회 쿼리(SELECT·CTE·UNION 등)를 파싱한 뒤 PostgreSQL 표준형으로 직렬화합니다. 공백·주석·키워드 표기·비인용 식별자 대소문자와 끝 세미콜론 차이를 정리하고, 문자열 리터럴과 인용 식별자는 보존합니다. 별칭 이름이나 조건·조인 순서를 임의로 바꾸는 최적화는 적용하지 않습니다. 복수 문장, 비조회 문장과 파싱할 수 없는 문자열은 파싱 실패로 기록합니다.

| `summary` 지표 | 계산 기준 |
| --- | --- |
| `exact_match_rate` | API 응답 성공 항목 중 정답 SQL과 생성 SQL 원문이 정확히 같은 비율. 앞뒤 공백도 비교합니다. |
| `normalized_evaluated_count` | API 응답에 성공했고 정답 SQL을 정규화할 수 있는 항목 수 |
| `normalized_exact_match_rate` | 위 항목 중 정규화 SQL 문자열이 같은 비율 |
| `prediction_parse_success_rate` | API 응답 성공 항목 중 생성 SQL이 단일 조회 쿼리로 파싱된 비율 |
| `prediction_parse_failure_count` | API 응답에 성공했지만 생성 SQL을 파싱하지 못한 항목 수 |
| `reference_parse_failure_count` | 전체 입력 데이터셋에서 정답 SQL을 파싱하지 못한 항목 수 |
| `reference_normalization_failure_count` | 전체 입력에서 정답 SQL의 파싱 또는 PostgreSQL 변환에 실패한 항목 수 |

API가 정상 SQL 문자열을 반환했지만 생성 SQL의 파싱·정규화가 실패하면 API 성공 상태를 유지하며, 정답이 정규화 가능한 경우 정규화 일치 점수는 `false`입니다. 정답 SQL 자체를 정규화하지 못하면 해당 항목의 정규화 일치 점수는 `null`로 저장하고 그 지표의 분모에서 제외합니다. 원문 비교와 생성 SQL 파싱 평가는 계속 수행합니다. 현재 데이터셋에는 기본 Oracle 설정으로 파싱되지 않는 MySQL식 변수 문법의 정답 10건이 있으며 별도로 집계합니다.

HTTP·API·타임아웃·연결·응답 형식 오류는 기록하고 다음 질문으로 진행합니다. 호출 실패 항목의 `metrics`는 `null`이며 품질 지표에서 제외하고, 호출 성공률을 함께 저장합니다. 평가할 항목이 없는 지표는 `null`입니다.

정규화 일치는 SQL 구조의 비교 지표이며 DB 실행 결과 일치를 평가하지 않습니다. 파싱에 성공해도 실제 DB의 테이블·컬럼·함수에 대한 실행 검증을 통과했다는 의미는 아닙니다. [SQLGlot 공식 문서](https://sqlglot.com/sqlglot.html)

### SQL 결과 구조·시간·토큰 사용량

결과에는 `evaluation_type: "sql_generation"`, `format_version`, `configuration`, `started_at`, `updated_at`, `state`, `summary`, `results`를 저장합니다. `configuration`에는 엔드포인트·서버·입력 경로·데이터셋 및 카탈로그 SHA-256·SQL 방언·파서 버전·지표 버전을 기록합니다.

`results`는 데이터셋 순서로 정렬하고 각 항목에 다음 내용을 저장합니다.

- `index`, `id`, `category`, `question`, `input_table_names`
- `expected_sql`, `predicted_sql`, `normalized_expected_sql`, `normalized_predicted_sql`
- `metrics`: `exact_match`, `normalized_exact_match`, `prediction_parse_success`
- `evaluation_errors`: 정답·생성 SQL 각각의 파싱 또는 변환 오류
- `status`, `http_status`, `error`, `completed_at`
- `latency_seconds`, `api_elapsed_time`, `model_name`, `question_token`, `answer_token`

스키마 DDL과 설명 본문은 결과에 중복 저장하지 않습니다. `index`는 0부터 시작하는 데이터셋 위치이며 `id`가 없으면 위치를 ID로 사용합니다. `status`는 API 응답 처리 상태이고, SQL의 정답 여부는 `metrics`로 판단합니다.

전체·처리·성공·실패·미처리 건수와 `success_rate`도 집계합니다. 성공률의 분모는 처리된 항목 수입니다. `mean_latency_seconds`는 성공 항목의 HTTP 왕복·응답 검증 평균 시간으로 SQL 파싱·채점 시간을 제외합니다. `mean_api_elapsed_time`은 유효한 서버 `elapsed_time`을 받은 성공 항목의 평균입니다.

입출력 토큰은 API 응답의 `meta_data`에 있는 `question_token`, `answer_token`을 사용합니다. 입력 토큰에는 질문뿐 아니라 프롬프트·스키마도 포함됩니다. 둘 다 유효한 성공 항목 수를 `token_usage_count`에 저장하고 해당 항목의 `total_question_token`, `total_answer_token`, `mean_question_token`, `mean_answer_token`을 집계합니다. 사용량이 제공되지 않으면 토큰 필드는 `null`이며 비용을 임의로 추정하지 않습니다.

### SQL 평가 중간 저장과 재실행

매 항목 처리 후 결과를 임시 파일에 작성하고 원자적으로 교체합니다. `RESUME=True`에서는 API 응답 성공 항목을 재사용하고 실패·미처리 항목만 다시 호출합니다. 정답 불일치나 생성 SQL 파싱 실패가 있는 API 성공 항목도 재사용하며, 저장된 SQL에서 지표를 다시 계산합니다.

엔드포인트·서버 주소·데이터셋 및 카탈로그 해시·입력 스키마 필드·SQL 방언·정규화 방언·평가 및 SQLGlot 버전이 같은 결과만 재사용합니다. 입력 필드는 `configuration.schema_field`에 기록합니다. 이 필드가 없는 기존 결과는 `predicted_schema` 평가로 해석하여 기존 평가의 재개를 지원합니다. 설정 불일치나 손상된 결과는 기존 파일을 보존하고 실행을 중단합니다. 시간 제한만 늘려 다시 실행할 수 있습니다.

이름이 다른 데이터셋은 각각의 결과 파일에 저장합니다. 같은 이름의 데이터셋은 두 SQL 생성 엔드포인트 모두 같은 결과 경로를 사용하므로, 엔드포인트가 다른 기존 결과는 `RESUME=True`로 재사용하지 않습니다. 같은 결과 파일의 데이터셋 내용·엔드포인트·모델·프롬프트·서버 측 설정을 바꾸어 전체 SQL을 새로 생성하려면 `RESUME=False`를 사용합니다. 기존 실험을 별도로 보관하려면 실행 전에 결과 파일을 복사합니다. 같은 결과 파일을 사용하는 평가는 한 프로세스씩 실행합니다.

`Ctrl+C`로 중단하면 완료한 결과를 저장하고 종료 코드 130을 반환합니다. 전체 처리를 완료하면 API 호출·응답 오류가 없을 때 0, 실패가 남았을 때 1을 반환합니다. 설정·입력·저장 오류도 1입니다. 종료 코드는 SQL 정답률이 아닌 요청·입출력 처리 상태를 나타냅니다.

```bash
python3 -m pytest tests/test_sql_generation_evaluation.py -q
```

테스트는 모의 HTTP로 두 엔드포인트의 요청 구성과 정답 정보 제외, SQL 정규화·구문 실패·집계 분모, 시간·토큰 사용량, 저장·재개를 검증합니다. 실제 모델 API나 DB는 호출하지 않습니다.

## 정답 스키마 기반 SQL 생성 성능 평가

`evaluation/src/evaluate_gold_schema_sql_generation.py`는 `question`과 정답 `schema`의 DDL 리스트로 SQL을 생성하고, `sql`을 정답으로 평가합니다. 기존 SQL 평가 공통 로직을 사용하며 입력 스키마 필드를 `schema`로 지정합니다.

코드 상단에서 다음 설정을 수정합니다. `ENDPOINT`는 `/generate_sql/llm_based_generate_sql_open_ai` 또는 `/generate_sql/llm_based_generate_sql`을 지원합니다.

```python
ENDPOINT = "/generate_sql/llm_based_generate_sql_open_ai"
DATASET_PATH = "evaluation/dataset/gold_schema_test_set.json"
BASE_URL = "http://localhost:31065"
TIMEOUT_SECONDS = 300.0
RESUME = True
GOLD_SQL_DIALECT = "oracle"
GENERATED_SQL_DIALECT = "postgres"
```

`schema`의 DDL을 `CATALOG_PATH`(기본 `dataset/db_schema.json`)와 정확히 매핑하여 대응하는 테이블명·DDL·카탈로그 설명을 요청의 `schema`로 구성합니다. 테이블명은 입력 DDL에서 매핑하며 데이터셋의 `tables`를 요청 구성에 사용하지 않습니다. DDL 순서와 중복은 그대로 유지합니다. 현재 정답 스키마 테스트셋에는 5,787개 항목이 있습니다.

API에는 `query=question`, `history=[]`와 위에서 구성한 `schema`만 전달합니다. 정답 `sql`은 채점에만 사용하고 `predicted_schema`를 포함한 다른 필드는 요청에 전달하지 않습니다. `schema`가 없거나 비어 있으면 예측 스키마로 대체하지 않고 API 호출 전에 오류로 종료합니다. 미등록·모호한 DDL과 잘못된 입력의 처리 규칙은 기존 SQL 평가와 같습니다.

```bash
python3 evaluation/src/evaluate_gold_schema_sql_generation.py
```

필요한 평가 의존성은 기존과 같이 `requirements-dev.txt`로 설치합니다. 상대 경로는 프로젝트 루트 기준입니다. 기본 데이터셋의 결과는 다음 파일에 저장합니다.

```text
evaluation/result/gold_schema_test_set.json
```

다른 데이터셋 경로를 지정하면 동일하게 `evaluation/result/{dataset_name}.json`에 저장합니다. 문자열 정확일치율, 정규화 정확일치율, 생성 SQL 파싱 성공률, 오류·시간·토큰 집계와 정답 파싱 실패 처리도 기존 SQL 평가와 같습니다. 실제 DB에서 SQL을 실행하지 않습니다.

결과의 `configuration.schema_field`는 `"schema"`입니다. 기존 예측 스키마 평가는 `"predicted_schema"`이며 서로의 결과를 재사용하지 않습니다. 선택 필드가 없는 과거 결과도 예측 스키마 평가로 해석하므로 정답 스키마 평가에는 재사용하지 않습니다. 같은 데이터셋에서 입력 필드 또는 엔드포인트를 바꾸어 새 평가를 시작하려면 `RESUME=False`를 사용합니다.

매 항목 후 저장하고 성공 응답은 재사용하며 실패·미처리 항목만 다시 호출합니다. 설정·해시 검증, 원자적 저장, 종료 코드와 모델 변경 후 재평가 규칙은 공통 SQL 평가와 같습니다. 검증용 테스트는 모의 HTTP만 사용합니다.

```bash
python3 -m pytest tests/test_gold_schema_sql_generation_evaluation.py tests/test_sql_generation_evaluation.py -q
```
