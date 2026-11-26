import json

from app.schemas.request import LlmBasedGenerateSqlRequest


SQL_GENERATION_SYSTEM_PROMPT = """당신은 자연어 요청을 PostgreSQL SQL로 변환하는 전문가입니다.

현재 사용자 메시지의 query와 schema를 기준으로 SQL 한 문장을 생성하세요.
이전 대화는 현재 질의의 생략된 대상, 지시어, 조건을 이해하는 문맥으로 사용하세요.
현재 질의에서 조건을 변경하면 현재 질의를 우선하세요.

작성 규칙:
- 제공된 schema의 테이블 이름, 컬럼 이름, DDL과 설명만 근거로 사용하세요.
- schema.table_name, schema.schema, schema.description의 같은 위치는 같은 테이블입니다.
- 제공되지 않은 테이블, 컬럼, 조인 키나 스키마에서 알 수 없는 사실을 만들어 내지 마세요.
- 결과는 PostgreSQL 문법이어야 합니다. 입력 DDL에 VARCHAR2, NUMBER 등 Oracle 형식이
  있더라도 PostgreSQL에 맞는 함수, 형 변환, 날짜 연산, 행 제한 문법을 사용하세요.
- 현재 질의에 답하는 데 필요한 조건, 집계, 조인, 정렬을 반영하세요.
- 설명, 주석으로 된 답변, Markdown 코드 블록을 덧붙이지 마세요.
- 구조화 응답의 sql 필드에는 생성한 SQL 한 문장만 넣으세요.
"""


def build_sql_generation_messages(request: LlmBasedGenerateSqlRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SQL_GENERATION_SYSTEM_PROMPT}]
    messages.extend(message.model_dump() for message in request.history[-3:])
    messages.append({
        "role": "user",
        "content": json.dumps({
            "query": request.query,
            "schema": request.schema_info.model_dump(mode="json", by_alias=True),
        }, ensure_ascii=False),
    })
    return messages
