"""Independent configuration; never loads the sibling API's credentials."""

from pathlib import Path
import math
import os
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def http_url(value: str, label: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{label}에는 인증정보·쿼리가 없는 HTTP(S) URL을 설정하세요.")
    return value


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=31067, ge=1, le=65535)
    mcp_url: str = "http://127.0.0.1:31068/mcp"
    llm_api_base: str = ""
    llm_model_name: str = "DeepSeek-V4-Flash-0731"
    openai_api_key: str = Field(default="", repr=False)
    openai_llm_model_name: str = "gpt-6-luna"
    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_llm_model_name: str = "deepseek/deepseek-v4-flash-0731"
    max_llm_rounds: int = Field(default=12, ge=1)
    max_tool_calls: int = Field(default=32, ge=1)
    request_timeout_seconds: float = Field(default=180, gt=0)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    mcp_timeout_seconds: float = Field(default=60, gt=0)

    @field_validator("mcp_url")
    @classmethod
    def validate_mcp_url(cls, value: str) -> str:
        return http_url(value, "MCP_URL")

    @field_validator("request_timeout_seconds", "llm_timeout_seconds", "mcp_timeout_seconds")
    @classmethod
    def finite_timeout(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("시간 제한은 유한한 양수여야 합니다.")
        return value

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "Settings":
        values = {**dotenv_values(env_path or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
        names = {
            "host": "AGENT_HOST", "port": "AGENT_PORT", "mcp_url": "MCP_URL",
            "llm_api_base": "LLM_API_BASE", "llm_model_name": "LLM_MODEL_NAME",
            "openai_api_key": "OPENAI_API_KEY", "openai_llm_model_name": "OPENAI_LLM_MODEL_NAME",
            "openrouter_api_key": "OPENROUTER_API_KEY",
            "openrouter_llm_model_name": "OPENROUTER_LLM_MODEL_NAME",
            "max_llm_rounds": "AGENT_MAX_LLM_ROUNDS", "max_tool_calls": "AGENT_MAX_TOOL_CALLS",
            "request_timeout_seconds": "AGENT_REQUEST_TIMEOUT_SECONDS",
            "llm_timeout_seconds": "LLM_TIMEOUT_SECONDS", "mcp_timeout_seconds": "MCP_TIMEOUT_SECONDS",
        }
        return cls(**{field: values[name] for field, name in names.items() if values.get(name) is not None})
