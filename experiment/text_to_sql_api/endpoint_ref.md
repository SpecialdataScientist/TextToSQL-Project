# 엔드포인트 동작 참고

현재 구현된 업무 API **25개**를 정리한 문서입니다. 모두 **POST**이며 일반 버전 10개, OpenAI 버전 10개, OpenRouter 버전 5개입니다. 아래 내용은 실제 라우트·요청 모델·서비스 구현을 기준으로 합니다.

서버 기본 포트는 `31065`이며 `API_PORT` 환경변수 또는 `--port` 실행 인자로 변경할 수 있습니다. API 명세는 `/docs`, `/openapi.json`에서 확인합니다. 설치·실행과 개별 예시는 [README](README.md)를 참고하세요.

## 1. 전체 목록

같은 행의 API는 같은 처리 계열이며 모델 공급자와 저장소 설정이 다릅니다. `—`는 해당 버전이 구현되어 있지 않다는 뜻입니다.

| 기능 | 일반 | 평가 완료 | OpenAI | 평가 완료 | OpenRouter | 평가 완료 |
| --- | --- | --- | --- | --- | --- | --- |
| 스키마 증분 인덱싱 | `/retrieve/indexing_schema` | — | `/retrieve/indexing_schema_open_ai` | — | — | — |
| Triplet 증분 인덱싱 | `/retrieve/indexing_triplet` | — | `/retrieve/indexing_triplet_open_ai` | — | — | — |
| 스키마 임베딩 검색 | **`/retrieve/embedding_based_schema`** | O | **`/retrieve/embedding_based_schema_open_ai`** | O | — | — |
| Triplet 임베딩 검색 | **`/retrieve/embedded_triplet_based_schema`** | O | **`/retrieve/embedded_triplet_based_schema_open_ai`** | O | — | — |
| 전체 카탈로그 LLM 검색 | `/retrieve/llm_based_schema` | — | **`/retrieve/llm_based_schema_open_ai`** | O | **`/retrieve/llm_based_schema_open_router`** | O |
| RDF 확장 후 LLM 재선별 | `/retrieve/triplet_based_schema` | — | `/retrieve/triplet_based_schema_open_ai` | — | `/retrieve/triplet_based_schema_open_router` | — |
| LLM 시작점·RDF 전체 확장 | `/retrieve/only_triplet_based_schema` | — | **`/retrieve/only_triplet_based_schema_open_ai`** | 1_1 / 1_2 | **`/retrieve/only_triplet_based_schema_open_router`** | 1_1 / 1_2 |
| 임베딩 시작점·RDF 전체 확장 | **`/retrieve/hybrid_only_based_schema`** | 1_1 / 1_2 | **`/retrieve/hybrid_only_based_schema_open_ai`** | 1_1 / 1_2 | — | — |
| RDF triplet 전체 생성 | `/retrieve/generate_triplet` | — | `/retrieve/generate_triplet_open_ai` | — | `/retrieve/generate_triplet_open_router` | — |
| SQL 생성 | `/generate_sql/llm_based_generate_sql` | — | `/generate_sql/llm_based_generate_sql_open_ai` | — | `/generate_sql/llm_based_generate_sql_open_router` | — |

### 검색 방식 비교

| 계열 | 후보·시작점 선택 | RDF 확장 | 최종 선택 | 반환 순서 | LLM 호출 단계 수 |
| --- | --- | --- | --- | --- | --- |
| `embedding_based_schema` | 스키마 벡터 유사도 | 없음 | 상위 `top_k`개 | 유사도 순 | 0 |
| `embedded_triplet_based_schema` | 테이블별 최고 triplet 유사도 | 없음 | 고유 테이블 상위 `top_k`개 | 점수 내림차순, 동점은 이름 오름차순 | 0 |
| `llm_based_schema` | 전체 카탈로그를 LLM에 전달 | 없음 | LLM 선택 결과 | LLM 선택 순 | 1 |
| `triplet_based_schema` | LLM으로 시작점 선택 | 있음 | 확장 후보를 LLM으로 재선별 | 최종 LLM 선택 순 | 2 |
| `only_triplet_based_schema` | LLM으로 시작점 선택 | 있음 | 전체 확장 후보 | 카탈로그 순 | 1 |
| `hybrid_only_based_schema` | Triplet 임베딩으로 시작점 선택 | 있음 | 전체 확장 후보 | 카탈로그 순 | 0 |

횟수는 정상적인 비어 있지 않은 입력의 논리적 LLM 단계 수입니다. 빈 카탈로그·시작점 예외는 각 절에 설명하며 SDK 재시도는 별도입니다. 임베딩 검색과 hybrid는 질의 임베딩 모델을 사용합니다. **Only-triplet도 시작점 선택에는 LLM을 사용합니다.**

## 2. 공통 입력과 응답

### 질의·히스토리

- `query`: 현재 자연어 질의인 필수 문자열입니다. 빈 문자열과 공백뿐인 값은 거부합니다.
- `history`: 오래된 순서의 메시지 리스트이며 생략 시 `[]`입니다. 메시지는 `role`과 `content`로 구성합니다. 역할은 `user` 또는 `assistant`, 내용은 공백뿐이지 않은 문자열입니다.
- 검색과 SQL 생성은 **`history[-3:]`과 현재 질의**를 사용합니다. 메시지 하나를 한 개로 세며 현재 질의는 최근 메시지 3개 제한에 포함하지 않습니다. 히스토리는 요청마다 전달하고 서버에서 누적하지 않습니다.
- 임베딩은 최근 메시지를 `role: content` 형식으로 결합하고 현재 질의를 뒤에 붙입니다. 히스토리가 없으면 질의 원문만 임베딩합니다. LLM에는 최근 메시지와 현재 질의·기능별 데이터를 메시지로 전달합니다.

```json
{
  "query": "그중 2학기 과목만 조회해줘",
  "history": [
    {"role": "user", "content": "학과별 과목 정보를 조회하고 싶어"},
    {"role": "assistant", "content": "학과와 과목 정보를 기준으로 조회하겠습니다."}
  ]
}
```

### `top_k`와 `max_hop`

| 계열 | `top_k` | `max_hop` |
| --- | --- | --- |
| 스키마·triplet 임베딩 검색 | 필수 양의 정수. 최종 반환 테이블 수의 상한 | 받지 않음 |
| 전체 카탈로그 LLM 검색 | 받지 않음 | 받지 않음 |
| Triplet·only-triplet·hybrid 검색 | 선택 정수, 기본 `-1`. 시작 테이블 수의 상한 | 선택 정수, 기본 `3` |

임베딩 검색은 후보가 부족하면 가능한 개수만 반환합니다. 관계 확장 검색의 `top_k=-1`은 시작점 무제한이고 양수는 최대 k개입니다. `0`은 허용하지 않습니다. **시작점 제한은 최종 반환 수에 적용하지 않습니다.**

관계 확장의 `max_hop`은 다음과 같습니다.

- `0`: 시작 테이블만 후보로 사용합니다.
- 양수: 지정한 깊이까지 탐색합니다. 양수 값 자체를 10으로 제한하지는 않습니다.
- `-1`: 최대 10-hop까지 탐색합니다. 이미 방문한 테이블은 건너뛰고, 다음 단계에 새 테이블이 없으면 종료합니다.

모든 시작점에서 들어오는 관계와 나가는 관계를 **양방향 BFS**로 탐색합니다. 관계 종류로 필터링하지 않으며, 결과는 방문 테이블의 합집합입니다. 예를 들어 `A — B — C`에서 시작점 `A`, `max_hop=2`이면 후보는 `A, B, C`입니다. 시작점이 여러 개면 각 시작점의 이웃이 함께 확장되므로 2-hop이어도 반환 테이블이 많을 수 있습니다.

`top_k`와 `max_hop`의 문자열·실수·불리언·`null` 입력은 거부합니다. 나머지 미정의 필드의 허용 여부는 각 요청 모델을 따릅니다.

### 공통 응답

성공 응답의 기본 구조는 다음과 같습니다. 아래 값은 형식 설명용 예시입니다.

```json
{
  "data": {
    "table_name": ["A"],
    "schema": ["CREATE TABLE A (id INTEGER);"],
    "description": ["예제 테이블"]
  },
  "meta_data": [],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.1
}
```

검색 API의 `data`는 **dict of list**이며 세 리스트의 같은 위치가 같은 테이블입니다. 필요한 스키마가 없으면 세 리스트가 모두 비어 있는 성공 응답을 반환할 수 있습니다. 비어 있지 않은 검색 결과의 `data`는 SQL 생성 요청의 `schema`로 전달할 수 있습니다.

| 필드 | 의미 |
| --- | --- |
| `data` | 검색 스키마·인덱싱 결과·생성 triplet·SQL 등 기능별 결과 |
| `meta_data` | 기능별 모델·토큰·개수·저장 위치 등의 사전 리스트 |
| `status`, `msg` | 성공·오류 상태와 메시지 |
| `elapsed_time` | 서버 처리 시간, 초 단위 |

서비스 처리 오류는 일반적으로 `status: "error"`, `data: null`, 빈 `meta_data`로 반환합니다. 요청 검증 오류인 422는 FastAPI의 `detail` 응답을 사용합니다.

## 3. 모델과 데이터 설정

설정은 프로젝트 `.env`와 프로세스 환경변수에서 읽으며 프로세스 환경변수가 우선합니다. 지원하는 실행 인자는 해당 환경변수·기본값보다 우선합니다. 아래 모델·경로는 **환경변수가 정의되지 않았을 때의 코드 기본값**입니다. 환경변수를 빈 문자열로 설정하면 기본값으로 보완하지 않으며, 관련 API 요청 시 설정 오류가 발생할 수 있습니다.

### 공급자별 모델

| 공급자 | 임베딩 | LLM | 인증 |
| --- | --- | --- | --- |
| 일반 | `EMBEDDING_API_BASE`, 기본 모델 `BAAI/bge-m3` (`--embedding_model_name`으로 변경) | `LLM_API_BASE`, `LLM_MODEL_NAME` 기본 `DeepSeek-V4-Flash-0731` (`--llm_api_base`, `--model_name` 지원) | 두 클라이언트 모두 `dummy` 키 사용 |
| OpenAI | 공식 OpenAI 주소, `OPENAI_EMBEDDING_MODEL_NAME` 기본 `text-embedding-3-small` | 공식 OpenAI 주소, `OPENAI_LLM_MODEL_NAME` 기본 `gpt-6-luna` | `OPENAI_API_KEY` |
| OpenRouter | 구현된 임베딩 계열 없음 | 공식 OpenRouter 주소, `OPENROUTER_LLM_MODEL_NAME` 기본 `deepseek/deepseek-v4-flash-0731` | `OPENROUTER_API_KEY` |

일반·OpenAI·OpenRouter는 같은 기능 서비스를 재사용하지만 클라이언트와 설정을 별도로 주입합니다. API 키는 요청 JSON에 넣지 않습니다. OpenAI·OpenRouter 설정은 해당 API를 요청할 때 검증하므로 키가 없어도 서버 시작은 가능합니다.

LLM 호출은 구조화 응답과 서버 측 결과 검증을 사용합니다. 일반 모델은 temperature 0이며 DeepSeek 모델의 thinking을 비활성화합니다. OpenAI의 `gpt-6-luna` 계열에는 `reasoning_effort="none"`을 적용합니다. OpenRouter는 temperature 0, `reasoning.enabled=false`, `provider.require_parameters=true`를 전달합니다. LLM 클라이언트의 기본 제한 시간은 60초, 최대 재시도는 2회입니다.

### 카탈로그·저장소

| 용도 | 일반 | OpenAI | OpenRouter |
| --- | --- | --- | --- |
| 원본 카탈로그 | 세 공급자 모두 `dataset_path`, 기본 `./dataset/db_schema.json`; `--dataset_path`로 변경 | 공통 | 공통 |
| 스키마 인덱스 루트 | `EMBEDDED_SCHEMA`, 기본 `./embedded_schema` | `EMBEDDED_SCHEMA_OPEN_AI`, 기본 `./embedded_schema_open_ai` | — |
| Triplet 인덱스 루트 | `EMBEDDED_TRIPLET`, 기본 `./embedded_triplet` | `EMBEDDED_TRIPLET_OPEN_AI`, 기본 `./embedded_triplet_open_ai` | — |
| RDF 루트 | `RDF_TRIPLET`, 기본 `./rdf_triplet` | `RDF_TRIPLET_OPEN_AI`, 기본 `./rdf_triplet_open_ai` | `RDF_TRIPLET_OPEN_ROUTER`, 기본 `./rdf_triplet_open_router` |

상대 경로는 실행 디렉터리와 관계없이 프로젝트 루트를 기준으로 해석합니다.

- 스키마 인덱스: `<스키마 인덱스 루트>/db_schema/` 아래 `index.faiss`, `metadata.json`, `raw_data.json`.
- Triplet 인덱스: `<triplet 인덱스 루트>/triplets/` 아래 같은 이름의 세 파일.
- RDF 파일: `<RDF 루트>/triplets.json`.

OpenAI 인덱스는 대응하는 일반 인덱스와 같은 경로를 사용할 수 없습니다. OpenAI RDF 생성·LLM 관계 검색·hybrid 검색은 일반 RDF와 경로를 분리하도록 검증합니다. OpenRouter RDF 생성·관계 검색은 일반·OpenAI RDF 모두와 경로를 분리하도록 검증합니다.

## 4. 기능별 상세 동작

### 4.1 스키마 증분 인덱싱

대상: `indexing_schema`, `indexing_schema_open_ai`. **요청 본문은 없습니다.**

1. 현재 카탈로그 전체를 읽어 필수 문자열·중복 테이블명을 검증합니다.
2. 인덱스 저장소를 잠그고 기존 모델·벡터·메타데이터·원본의 일치 여부를 확인합니다.
3. 기존 인덱스에 없는 **`table_name`**만 신규 항목으로 선택합니다.
4. 테이블명과 설명을 임베딩하고 벡터를 정규화하여 FAISS 코사인 유사도용 인덱스에 추가합니다. 원본 DDL·설명도 함께 저장합니다.
5. 인덱스·메타데이터·원본 스냅샷을 교체합니다. 저장 실패 시 기존 스냅샷을 보존하거나 복구합니다.

같은 이름의 DDL·설명이 변경되어도 다시 임베딩하거나 저장된 원본을 갱신하지 않습니다. 카탈로그에서 삭제한 테이블도 기존 인덱스에 남습니다. 신규 항목이 없으면 임베딩과 인덱스 파일 쓰기를 생략합니다.

`data`는 다음 형식입니다.

```json
{
  "added_count": 1,
  "skipped_count": 2,
  "total_count": 5,
  "added_tables": ["A"]
}
```

`added_count`·`skipped_count`는 이번 입력에서 추가·건너뛴 개수이고 `total_count`는 저장된 누적 테이블 수입니다. 입력에서 삭제한 항목이 남을 수 있으므로 누적 수가 이번 입력 수와 같을 필요는 없습니다. 메타데이터는 `embedding_model`, `dimension`, `index_directory`입니다.

### 4.2 Triplet 증분 인덱싱

대상: `indexing_triplet`, `indexing_triplet_open_ai`. **요청 본문은 없습니다.**

1. 해당 공급자의 `<RDF 루트>/triplets.json`을 읽고 관계 형식을 검증합니다.
2. **주체 테이블명·관계 문자열·대상 테이블명**으로 SHA-256 ID를 계산하여 기존 ID와 이번 입력의 중복을 제거합니다.
3. 새 관계의 주체 이름·설명, 관계, 대상 이름·설명을 임베딩합니다.
4. 정규화 벡터와 원본 triplet을 기존 인덱스에 추가하고 스냅샷을 교체합니다.

일반 버전은 `RDF_TRIPLET/triplets.json`을 우선 읽고, 파일이 없을 때만 `RDF_TRIPLET_OPEN_ROUTER/triplets.json`을 대신 읽습니다. 기본 파일이 `[]`이거나 형식·읽기 오류가 있으면 대체하지 않습니다. 임베딩은 기존 `EMBEDDING_API_BASE`와 모델을 사용하고 `EMBEDDED_TRIPLET`에 저장하며, OpenRouter API 키는 필요하지 않습니다. 실제 입력 경로와 대체 사유는 로그에 기록합니다.

OpenAI 버전은 `RDF_TRIPLET_OPEN_AI`에서 읽어 `EMBEDDED_TRIPLET_OPEN_AI`에 저장하고 OpenAI 임베딩 모델을 사용합니다. 일반·OpenRouter 입력으로 대체하지 않습니다. 일반 버전의 두 입력 파일이 모두 없거나 선택한 파일이 잘못되면 기존 오류 형식으로 500을 반환하고 기존 인덱스를 보존합니다.

설명만 바뀐 같은 관계는 기존 ID로 판단하여 갱신하지 않습니다. RDF에서 제거된 관계도 인덱스에 남습니다. RDF 전체 생성과 triplet 증분 인덱싱은 별도 작업이며, RDF를 재생성해도 인덱스가 자동 동기화되지는 않습니다.

```json
{
  "added_count": 1,
  "skipped_count": 2,
  "total_count": 5,
  "added_triplet_ids": ["관계의 SHA-256 ID"]
}
```

`skipped_count`에는 입력 내 중복도 포함됩니다. 메타데이터는 스키마 인덱싱과 같은 `embedding_model`, `dimension`, `index_directory`입니다. 두 인덱싱 계열 모두 처음부터 빈 입력이고 기존 인덱스도 없으면 0개 성공 결과를 반환하지만 검색할 스냅샷은 생성하지 않습니다. 이때 `dimension`은 `null`일 수 있습니다.

### 4.3 스키마 임베딩 검색

대상: `embedding_based_schema`, `embedding_based_schema_open_ai`.

입력은 필수 `query`, 필수 양의 정수 `top_k`, 선택 `history`입니다. 해당 공급자의 스키마 인덱싱이 먼저 필요합니다.

1. 저장된 스키마 인덱스와 원본 스냅샷을 읽습니다.
2. 질의·최근 대화를 한 번 임베딩하고 인덱스와 벡터 차원을 확인합니다.
3. 벡터 유사도 상위 `min(top_k, 저장 개수)`개를 검색합니다.
4. **인덱스에 저장된** 테이블명·DDL·설명을 유사도 순으로 반환합니다. 현재 카탈로그를 다시 읽어 DDL을 갱신하지 않습니다.

LLM·RDF는 사용하지 않습니다. 유효한 빈 인덱스는 질의 임베딩 없이 빈 결과를 반환합니다. 메타데이터는 `embedding_model`, `top_k`, `retrieved_count`입니다.

### 4.4 Triplet 임베딩 검색

대상: `embedded_triplet_based_schema`, `embedded_triplet_based_schema_open_ai`.

입력은 필수 `query`, 필수 양의 정수 `top_k`, 선택 `history`입니다. 해당 공급자의 triplet 인덱싱이 먼저 필요합니다.

1. 저장된 triplet 인덱스를 읽고 현재 카탈로그에 테이블을 매핑합니다.
2. 질의·최근 대화를 임베딩하고 모든 triplet의 유사도를 검색합니다.
3. 각 triplet의 주체·대상에 점수를 배정하고 **테이블별 최고 유사도**를 사용합니다. 합계나 평균을 사용하지 않습니다.
4. 고유 테이블을 점수 내림차순, 동점은 이름 오름차순으로 정렬하여 최대 `top_k`개를 선택합니다.
5. **현재 카탈로그의** 원본 DDL·설명을 반환합니다. 카탈로그에 없는 인덱스 테이블은 제외합니다.

LLM과 그래프 확장은 사용하지 않습니다. RDF 파일을 직접 읽지 않으므로 검색 시 RDF 파일이 없어도 저장된 인덱스로 검색할 수 있습니다. 유효한 빈 인덱스 또는 매핑 가능한 테이블이 없으면 질의 임베딩 없이 빈 결과를 반환합니다.

메타데이터는 `embedding_model`, `top_k`, `retrieved_count`, `unmapped_table_count`입니다. 마지막 필드는 인덱스에는 있지만 현재 카탈로그에 없는 고유 테이블 수입니다.

### 4.5 전체 카탈로그 LLM 검색

대상: `llm_based_schema`, `llm_based_schema_open_ai`, `llm_based_schema_open_router`.

입력은 `query`, 선택 `history`입니다. `top_k`·`max_hop`은 받지 않습니다.

1. 현재 카탈로그의 전체 테이블명·설명·원본 DDL을 읽습니다.
2. 현재 질의·최근 대화와 함께 LLM에 한 번 전달해 필요한 테이블을 선택합니다.
3. 선택 이름이 카탈로그에 있는지 검증하고 중복을 제거합니다.
4. LLM 선택 순으로 현재 카탈로그의 원본 스키마를 반환합니다.

임베딩 인덱스·RDF는 필요하지 않습니다. 빈 카탈로그는 호출 없이, 빈 선택은 한 번 호출 후 빈 결과를 반환합니다. 설정과 카탈로그 검증은 먼저 수행합니다.

메타데이터는 `model_name`, `question_token`, `answer_token`, `table_count`, `retrieved_count`입니다.

### 4.6 RDF 확장 후 LLM 재선별 검색

대상: `triplet_based_schema`, `triplet_based_schema_open_ai`, `triplet_based_schema_open_router`.

입력은 `query`, 선택 `history`, 선택 `top_k=-1`, 선택 `max_hop=3`입니다. 해당 공급자의 RDF 생성이 먼저 필요하며 임베딩 인덱스는 필요하지 않습니다.

1. 현재 카탈로그와 해당 공급자의 RDF를 읽습니다. RDF에 카탈로그 밖 테이블이 있으면 오류로 처리합니다.
2. 전체 테이블명·설명과 질의·최근 대화로 LLM이 시작 테이블을 선택합니다. 이름 검증·중복 제거 후 양수 `top_k`를 적용합니다.
3. 시작점을 포함하여 RDF를 `max_hop`만큼 양방향 확장합니다.
4. 확장 후보의 원본 DDL·설명, 시작점과 관계를 같은 질의·최근 대화와 함께 LLM에 다시 전달합니다.
5. 최종 이름이 확장 후보 안에 있는지 검증하고 중복을 제거한 뒤 최종 LLM 선택 순으로 원본 스키마를 반환합니다.

확장 수보다 적은 테이블을 반환할 수 있고, 최종 수가 시작점 `top_k`보다 많아도 허용합니다. RDF 관계는 추론된 관계이며 최종 SQL의 확정된 조인 조건으로 사용하도록 강제하지 않습니다.

빈 카탈로그는 LLM 0회, 빈 시작점은 1회, 시작점이 있는 경우는 최종 선택이 비어 있어도 2회입니다. 메타데이터의 `question_token`·`answer_token`은 **두 단계 사용량의 합계**입니다.

### 4.7 LLM 시작점·RDF 전체 확장 검색

대상: `only_triplet_based_schema`, `only_triplet_based_schema_open_ai`, `only_triplet_based_schema_open_router`.

입력과 준비 데이터는 4.6과 같습니다.

1. LLM을 한 번 호출해 시작점을 선택하고 `top_k`를 적용합니다.
2. 해당 공급자의 현재 RDF를 `max_hop`만큼 양방향 확장합니다.
3. 시작점을 포함한 전체 확장 후보의 원본 스키마를 **카탈로그 순서대로** 반환합니다.

최종 LLM 재선별은 없습니다. `expanded_count == retrieved_count`이며 최종 개수 제한은 없습니다. 빈 카탈로그는 LLM 0회, 빈 시작점을 포함한 일반 선택은 1회입니다.

4.6과 4.7의 메타데이터는 `model_name`, `question_token`, `answer_token`, `seed_count`, `expanded_count`, `retrieved_count`, `graph_depth`입니다. `graph_depth`는 실제 수행한 단계 수가 아닌 **적용한 탐색 상한**이며 `max_hop=-1`이면 `10`입니다. Only-triplet의 토큰 수는 시작점 선택 한 단계의 사용량입니다.

### 4.8 임베딩 시작점·RDF 전체 확장 검색

대상: `hybrid_only_based_schema`, `hybrid_only_based_schema_open_ai`.

입력은 `query`, 선택 `history`, 선택 `top_k=-1`, 선택 `max_hop=3`입니다. 해당 공급자의 **triplet 인덱스와 사용할 RDF 파일 모두** 필요합니다.

1. Triplet 인덱스와 현재 카탈로그·RDF를 읽습니다.
2. 질의·최근 대화를 임베딩하고 4.4와 같은 테이블별 최고 유사도 순위로 고유 시작점을 선택합니다.
3. `top_k=-1`이면 카탈로그에 매핑 가능한 인덱스 테이블 전체를 시작점으로 사용합니다. 양수이면 최대 k개를 사용합니다.
4. **현재 RDF 파일**의 관계를 `max_hop`만큼 확장합니다. 인덱스에 함께 저장된 과거 관계를 그래프 탐색 원본으로 사용하지 않습니다.
5. 같은 카탈로그 스냅샷에서 원본 DDL·설명을 매핑하여 전체 후보를 카탈로그 순서대로 반환합니다.

일반 버전은 `RDF_TRIPLET/triplets.json`을 우선 읽고, 파일이 없을 때만 `RDF_TRIPLET_OPEN_ROUTER/triplets.json`을 대신 읽습니다. 빈 목록 `[]`, 형식·테이블 참조·권한·읽기 오류나 카탈로그 오류에는 대체하지 않습니다. 두 RDF 파일이 모두 없으면 404, 선택한 RDF의 형식·테이블 참조 오류는 409, 설정·카탈로그·읽기 오류는 500입니다. 실제 RDF 경로와 대체 사유는 로그에 기록합니다.

대체 파일을 사용해도 시작점 검색은 기존 `EMBEDDED_TRIPLET`과 `EMBEDDING_API_BASE`를 사용하며 OpenRouter API 키는 필요하지 않습니다. OpenAI 버전은 기존 `RDF_TRIPLET_OPEN_AI`만 사용합니다. 검색 중 원본 파일이나 인덱스를 갱신하지 않습니다.

LLM은 호출하지 않습니다. 유효한 빈 인덱스나 매핑 가능한 시작점이 없으면 질의 임베딩을 생략하지만 카탈로그·RDF는 로드·검증합니다. `expanded_count == retrieved_count`입니다.

메타데이터는 `embedding_model`, `top_k`, `seed_count`, `expanded_count`, `retrieved_count`, `graph_depth`, `unmapped_table_count`입니다. LLM 토큰 필드는 없습니다. OpenAI 버전도 OpenAI **임베딩** 설정을 사용하므로 `OPENAI_LLM_MODEL_NAME`은 요구하지 않습니다.

### 4.9 RDF triplet 전체 생성

대상: `generate_triplet`, `generate_triplet_open_ai`, `generate_triplet_open_router`. **요청 본문은 없습니다.**

1. 현재 카탈로그의 모든 테이블명·설명·원본 DDL을 읽습니다.
2. LLM에 한 번 전달해 주체 테이블명·관계·대상 테이블명을 생성합니다.
3. 테이블명이 카탈로그에 있는지 검증하고 원본 설명을 복원하며 같은 주체·관계·대상 중복을 제거합니다.
4. 해당 공급자의 `<RDF 루트>/triplets.json`을 전체 목록으로 원자적으로 교체합니다. 증분 추가가 아닙니다.

`data`는 다음 형태의 triplet 리스트입니다.

```json
[
  [
    {"table_name": "A", "description": "테이블 A"},
    "references",
    {"table_name": "B", "description": "테이블 B"}
  ]
]
```

빈 카탈로그이면 LLM을 호출하지 않고 `[]`를 저장합니다. LLM이 빈 관계를 선택해도 `[]`로 전체 교체합니다. 검증·저장 실패 시 기존 RDF를 보존합니다. 임베딩 인덱싱은 자동 실행하지 않습니다.

메타데이터는 `model_name`, `question_token`, `answer_token`, `table_count`, `triplet_count`, `triplet_file`입니다. `triplet_file`은 서버의 저장 파일 경로입니다.

### 4.10 SQL 생성

대상: `llm_based_generate_sql`, `llm_based_generate_sql_open_ai`, `llm_based_generate_sql_open_router`.

입력은 필수 `query`, 필수 `schema`, 선택 `history`입니다. `schema`는 테이블명·DDL·설명의 **비어 있지 않은 동일 길이 리스트**이며 각 값은 공백뿐이지 않은 문자열이어야 합니다.

```json
{
  "query": "전체 행의 id를 조회해줘",
  "history": [],
  "schema": {
    "table_name": ["A"],
    "schema": ["CREATE TABLE A (id INTEGER);"],
    "description": ["예제 테이블"]
  }
}
```

1. 현재 질의·최근 대화·입력 스키마를 LLM에 한 번 전달합니다.
2. PostgreSQL SQL 한 문장을 생성하도록 지시하고 구조화 응답의 SQL 문자열을 추출합니다.
3. 앞뒤 공백을 제거하고 빈 값·Markdown 코드 펜스로 시작하는 결과를 거부한 뒤 `data`에 문자열로 반환합니다.

카탈로그·RDF·임베딩 인덱스를 직접 읽지 않습니다. SQL을 데이터베이스에서 실행하거나 SQL 구문·정답 일치 여부를 평가하지 않습니다. API 자체가 `.sql` 파일을 생성하지도 않습니다.

```json
{
  "data": "SELECT id FROM A;",
  "meta_data": [{"model_name": "사용한 모델 ID", "question_token": 100, "answer_token": 20}],
  "status": "success",
  "msg": "success",
  "elapsed_time": 0.1
}
```

세 공급자 모두 같은 SQL 요청·응답 형식을 사용합니다. 검색 계열은 엔드포인트별 요청·응답 모델을 정의한 경우에도 공통 스키마 리스트 형식을 유지합니다. 실제 타입은 [요청 모델](app/schemas/request.py), [응답 모델](app/schemas/response.py)을 참고하세요.

## 5. 준비 및 호출 순서

| 사용할 기능 | 먼저 할 작업 |
| --- | --- |
| 스키마 임베딩 검색 | 카탈로그 준비 → 같은 공급자의 스키마 인덱싱 → 검색 |
| Triplet 임베딩 검색 | RDF 준비·생성 → 같은 공급자의 triplet 인덱싱 → 검색 |
| LLM 스키마 검색 | 카탈로그·LLM 설정 준비 → 검색 |
| Triplet·only-triplet 검색 | 카탈로그·LLM 설정 준비 → 같은 공급자의 RDF 생성 → 검색 |
| Hybrid 검색 | 카탈로그·임베딩 설정 준비 → 같은 공급자의 RDF 생성 → triplet 인덱싱 → 검색 |
| SQL 생성 | 스키마를 직접 준비하거나 검색 → 검색 `data`를 요청 `schema`로 전달 → SQL 생성 |

RDF 파일을 이미 준비했다면 생성 단계를 생략할 수 있습니다. Hybrid는 검색 시 LLM을 호출하지 않지만, 준비 과정의 RDF 생성 API는 LLM을 사용합니다. OpenRouter의 RDF 생성 결과는 OpenRouter triplet·only-triplet 검색에서 사용합니다.

인덱싱은 신규 항목만 추가하고 생성은 RDF 전체를 교체합니다. 원본 수정·삭제가 기존 임베딩에 자동 반영되는 전체 동기화 방식은 아닙니다. 조회 API는 스키마·RDF·인덱스 파일을 수정하지 않습니다. 인덱싱·RDF 발행 작업은 요청이 취소되어도 이미 진행 중인 저장을 완료할 수 있습니다.

## 6. 오류 처리

| HTTP 상태 | 대표적인 원인 |
| --- | --- |
| 200 | 정상 처리. 검색의 빈 결과와 생성의 빈 triplet 목록도 포함 |
| 404 | 검색에 필요한 인덱스 스냅샷 또는 관계 검색의 RDF 파일이 없음 |
| 409 | 기존 인덱스 손상·모델/차원 불일치, 관계 검색의 잘못된 RDF·카탈로그 밖 테이블 참조 |
| 422 | 누락·공백 질의, 잘못된 히스토리, 허용하지 않는 `top_k`·`max_hop`, 잘못된 SQL 입력 스키마 등 요청 검증 오류 |
| 500 | 키·모델·URL·저장 경로 설정 오류, 카탈로그 오류, 파일 읽기·저장 실패 |
| 502 | 임베딩·LLM 통신/인증/요청 한도 오류, 잘못된 임베딩 벡터, 거부·중단·유효하지 않은 LLM 결과 |

404는 조회 API의 준비 데이터 누락에 적용합니다. Triplet **인덱싱**의 입력 RDF 누락·형식 오류는 500이며, 존재하는 인덱스 스냅샷의 구성 파일이 일부 없거나 손상되면 409입니다. 조회 벡터와 인덱스의 차원 불일치는 409, 벡터 자체의 비정상 값은 502입니다.

서비스 오류 시 부분 스키마를 반환하지 않습니다. OpenAI·OpenRouter 키는 설정 객체의 문자열 표현에서 제외하고 오류 응답·로그에서 마스킹합니다.

## 7. 테스트와 구현 참고

각 API를 호출하는 스크립트는 [tests/scripts](tests/scripts)에 있으며 파일명은 `<엔드포인트 마지막 이름>_script.sh`입니다. 기본 요청 예시는 [tests/test_doc](tests/test_doc)에 있습니다. 예를 들어 OpenRouter triplet 검색은 다음과 같이 호출합니다.

```bash
bash tests/scripts/triplet_based_schema_open_router_script.sh
```

스크립트는 `BASE_URL`, `OUTPUT_DIR`, `--help`를 지원합니다. 검색·SQL 생성은 선택 요청 JSON 경로도 지원하며, 인덱싱·triplet 생성은 본문 없이 호출합니다. 기본 서버 주소는 스크립트마다 다를 수 있으므로 `--help`를 확인하거나 `BASE_URL`을 지정합니다.

스크립트의 기본 결과 저장 위치는 `tests/results/`입니다. `<엔드포인트 이름>_raw_1.json`과 `<엔드포인트 이름>_response_1.json`을 저장하고, SQL 생성 스크립트는 `_1.sql`, triplet 생성 스크립트는 `_1.json`으로 `data`를 추가 추출합니다. 이 클라이언트 파일들은 API 자체의 저장 동작과 구분됩니다.

구현 확인 위치:

- [검색·인덱싱·RDF 라우트](app/api/routes/retrieve.py), [SQL 라우트](app/api/routes/generate_sql.py)
- [공급자별 서비스 연결](app/api/build.py), [설정과 코드 기본값](app/core/config.py)
- [서비스 구현](app/services), [전체 사용법·평가 코드 안내](README.md)
