from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from .config import Settings, http_url
from .errors import AgentError
from .schemas import Provider


@dataclass(frozen=True)
class LlmProfile:
    base_url: str
    api_key: str = field(repr=False)
    options: dict[str, Any]


def resolve_profile(settings: Settings, provider: Provider) -> LlmProfile:
    if provider == "local":
        try:
            base = http_url(settings.llm_api_base, "LLM_API_BASE")
        except ValueError:
            raise AgentError("LLM_API_BASE에 유효한 HTTP(S) URL을 설정하세요.", 500) from None
        model = settings.llm_model_name.strip().removeprefix("openai/")
        key = "dummy"
        options: dict[str, Any] = {"model": model, "temperature": 0.0}
        if model.lower().startswith("deepseek"):
            options["extra_body"] = {"thinking": {"type": "disabled"}}
        model_field = "LLM_MODEL_NAME"
    elif provider == "openai":
        base = "https://api.openai.com/v1"
        key = settings.openai_api_key.strip()
        if not key:
            raise AgentError("OPENAI_API_KEY를 설정하세요.", 500)
        model = settings.openai_llm_model_name.strip().removeprefix("openai/")
        options = {"model": model}
        if model.startswith("gpt-6-luna"):
            options["reasoning_effort"] = "none"
        model_field = "OPENAI_LLM_MODEL_NAME"
    else:
        base = "https://openrouter.ai/api/v1"
        key = settings.openrouter_api_key.strip()
        if not key:
            raise AgentError("OPENROUTER_API_KEY를 설정하세요.", 500)
        model = settings.openrouter_llm_model_name.strip()
        options = {
            "model": model, "temperature": 0.0,
            "extra_body": {"reasoning": {"enabled": False}, "provider": {"require_parameters": True}},
        }
        model_field = "OPENROUTER_LLM_MODEL_NAME"
    if not model.strip():
        raise AgentError(f"{model_field}에 모델명을 설정하세요.", 500)
    return LlmProfile(base_url=base, api_key=key, options=options)


def create_llm_client(settings: Settings, profile: LlmProfile) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=profile.base_url, api_key=profile.api_key,
        timeout=settings.llm_timeout_seconds, max_retries=2,
    )
