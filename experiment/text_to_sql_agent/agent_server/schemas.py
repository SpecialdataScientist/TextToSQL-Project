from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


Provider = Literal["local", "openai", "openrouter"]


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: StrictStr = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: StrictStr = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)
    provider: Provider = "local"
    table_name: list[StrictStr] = Field(
        default_factory=list,
        description="조회할 테이블명 배열. 생략하면 전체 도구를 검색하고, 빈 배열이면 조회 대상이 없습니다.",
    )

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("table_name")
    @classmethod
    def nonblank_table_names(cls, names: list[str]) -> list[str]:
        if any(not name.strip() for name in names):
            raise ValueError("table names must not be blank")
        return names


class ToolTrace(BaseModel):
    id: str
    function_name: str
    mcp_tool_name: str | None = None
    arguments: dict[str, Any] | str
    result: Any = None
    is_error: bool = False


class AnswerData(BaseModel):
    answer: str | None = None
    tool_calls: list[ToolTrace] = Field(default_factory=list)


class AgentResponse(BaseModel):
    status: Literal["success", "error"] = "success"
    msg: str = "success"
    data: AnswerData | None = None
    meta_data: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_time: float = 0


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = ""
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class CallArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
