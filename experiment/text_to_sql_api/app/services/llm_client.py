from openai import AsyncOpenAI

from app.core.config import resolve_llm_api_base, resolve_llm_model_name


def create_llm_client(settings, **client_options):
    options = {
        "base_url": resolve_llm_api_base(settings.llm_api_base),
        "api_key": "dummy",
        "timeout": 60.0,
        "max_retries": 2,
    }
    options.update(client_options)
    return AsyncOpenAI(**options)


def llm_completion_options(settings):
    model_name = resolve_llm_model_name(settings.model_name)
    options = {"model": model_name, "temperature": 0.0}
    if model_name.lower().startswith("deepseek"):
        options["extra_body"] = {"thinking": {"type": "disabled"}}
    return options
