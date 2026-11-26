from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, TypeAdapter, field_validator, model_validator


class ConversationHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: StrictStr = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def require_nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("history content must not be blank")
        return value


def build_embedding_text(query: str, history: list[ConversationHistoryMessage]) -> str:
    recent_history = history[-3:]
    if not recent_history:
        return query
    messages = [f"{message.role}: {message.content}" for message in recent_history]
    messages.append(f"user: {query}")
    return "\n\n".join(messages)


class EmbeddingBasedSchemaRequest(BaseModel):
    query: StrictStr = Field(min_length=1)
    top_k: StrictInt = Field(ge=1)
    history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        description=(
            "과거 대화 메시지를 오래된 순서로 전달합니다. 메시지 하나가 1 turn이며, "
            "history[-3:]을 현재 query 앞에 결합하여 검색합니다. 현재 query는 3 turn 제한에 포함하지 않습니다."
        ),
    )

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    def embedding_text(self) -> str:
        return build_embedding_text(self.query, self.history)


class EmbeddedTripletBasedSchemaRequest(EmbeddingBasedSchemaRequest):
    """Retrieve top_k distinct table schemas from the embedded triplet index."""

    model_config = ConfigDict(extra="forbid")


class EmbeddedTripletBasedSchemaOpenAiRequest(EmbeddedTripletBasedSchemaRequest):
    """Retrieve top_k distinct table schemas from the OpenAI triplet index."""


class LlmBasedSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: StrictStr = Field(min_length=1)
    history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        description=(
            "과거 메시지를 오래된 순서로 전달합니다. 메시지 하나가 1 turn이며, "
            "history[-3:]과 현재 query로 필요한 테이블을 선택합니다. 현재 query는 3 turn 제한에 포함하지 않습니다."
        ),
    )

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class LlmBasedSchemaOpenAiRequest(LlmBasedSchemaRequest):
    """OpenAI schema retrieval with the shared query and history validation rules."""


class LlmBasedSchemaOpenRouterRequest(LlmBasedSchemaRequest):
    """OpenRouter schema retrieval with the shared query and history validation rules."""


class TripletBasedSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: StrictStr = Field(min_length=1)
    history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        description=(
            "과거 메시지를 오래된 순서로 전달합니다. 메시지 하나가 1 turn이며, "
            "두 LLM 선택 단계에서 history[-3:]과 현재 query를 사용합니다."
        ),
    )
    max_hop: StrictInt = Field(
        default=3, ge=-1,
        description=(
            "관계 그래프의 최대 탐색 깊이입니다. 기본값은 3이며 0은 시작 테이블만 사용합니다. "
            "-1은 재방문 테이블을 건너뛰며 최대 10단계 탐색하고, 새 테이블이 없으면 종료합니다."
        ),
    )
    top_k: StrictInt = Field(
        default=-1, ge=-1,
        description=(
            "시작 테이블을 최대 top_k개 선택합니다. -1 또는 생략 시 개수 제한이 없습니다. "
            "양의 정수 또는 -1만 허용하며 0은 허용하지 않습니다. 최종 반환 개수에는 적용하지 않습니다."
        ),
    )

    @field_validator("top_k")
    @classmethod
    def require_positive_or_unlimited_top_k(cls, value: int) -> int:
        if value == 0:
            raise ValueError("top_k must be -1 or a positive integer")
        return value

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class TripletBasedSchemaOpenAiRequest(TripletBasedSchemaRequest):
    """OpenAI triplet retrieval request; shares the query and history validation rules."""


class TripletBasedSchemaOpenRouterRequest(TripletBasedSchemaRequest):
    """Select seeds and final schemas through OpenRouter with shared triplet validation."""


class OnlyTripletBasedSchemaRequest(TripletBasedSchemaRequest):
    """Select seeds with an LLM and return all schemas reached by graph expansion."""

    history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        description=(
            "과거 메시지를 오래된 순서로 전달합니다. 메시지 하나가 1 turn이며, "
            "시작 테이블 선정 LLM에 history[-3:]과 현재 query를 전달합니다."
        ),
    )


class OnlyTripletBasedSchemaOpenAiRequest(OnlyTripletBasedSchemaRequest):
    """Select seeds with OpenAI and return every schema reached by graph expansion."""


class OnlyTripletBasedSchemaOpenRouterRequest(OnlyTripletBasedSchemaRequest):
    """Select seeds with OpenRouter and return every schema reached by graph expansion."""


class HybridOnlyBasedSchemaRequest(TripletBasedSchemaRequest):
    """Select seeds from triplet embeddings and return all RDF-expanded schemas."""

    history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        description=(
            "과거 메시지를 오래된 순서로 전달합니다. history[-3:]과 현재 query를 결합하여 "
            "triplet 임베딩 인덱스에서 시작 테이블을 검색합니다."
        ),
    )

    def embedding_text(self) -> str:
        return build_embedding_text(self.query, self.history)


class HybridOnlyBasedSchemaOpenAiRequest(HybridOnlyBasedSchemaRequest):
    """Select seeds with OpenAI triplet embeddings and return all RDF-expanded schemas."""


class SqlGenerationSchema(BaseModel):
    table_name: list[StrictStr] = Field(min_length=1)
    ddl: list[StrictStr] = Field(alias="schema", min_length=1)
    description: list[StrictStr] = Field(min_length=1)

    @field_validator("table_name", "ddl", "description")
    @classmethod
    def require_nonblank_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Schema lists must contain nonblank strings")
        return values

    @model_validator(mode="after")
    def require_matching_lengths(self):
        if len(self.table_name) != len(self.ddl) or len(self.table_name) != len(self.description):
            raise ValueError("table_name, schema and description lists must have the same length")
        return self


class LlmBasedGenerateSqlRequest(BaseModel):
    query: StrictStr = Field(min_length=1)
    schema_info: SqlGenerationSchema = Field(
        alias="schema", description="스키마 검색 API 응답의 data를 그대로 전달합니다.",
    )
    history: list[ConversationHistoryMessage] = Field(
        default_factory=list,
        description="오래된 순서의 과거 메시지입니다. history[-3:]과 현재 query를 사용하며 메시지 하나가 1 turn입니다.",
    )

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class SchemaTable(BaseModel):
    model_config = ConfigDict(extra="allow")

    table_name: StrictStr = Field(min_length=1)
    ddl: StrictStr = Field(alias="schema", min_length=1)
    description: StrictStr = Field(min_length=1)

    @field_validator("table_name", "ddl", "description")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Schema fields must not be blank")
        return value

    @field_validator("table_name")
    @classmethod
    def require_exact_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("table_name must not have surrounding whitespace")
        return value

    def embedding_text(self) -> str:
        return f"table_name: {self.table_name}\ndescription: {self.description}"


SCHEMA_TABLES = TypeAdapter(list[SchemaTable])


def validate_unique_tables(tables: list[SchemaTable]) -> None:
    names = [table.table_name for table in tables]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate table_name values are not allowed")
