from openai import AsyncOpenAI

from app.core.config import OPENROUTER_API_BASE, resolve_project_path


def require_open_router_key(settings):
    key = settings.openrouter_api_key.strip()
    if not key:
        raise ValueError("OPENROUTER_API_KEY must be set in .env or the environment")
    return key


def open_router_model_name(settings):
    # OpenRouter model IDs include the provider prefix, which must be preserved.
    model = settings.model_name.strip()
    if not model:
        raise ValueError("OPENROUTER_LLM_MODEL_NAME must specify a model")
    return model


def validate_open_router_llm_settings(settings):
    require_open_router_key(settings)
    open_router_model_name(settings)


def validate_open_router_triplet_settings(settings, *, legacy_directory, open_ai_directory):
    validate_open_router_llm_settings(settings)
    root = resolve_project_path(settings.rdf_triplet)
    for name, directory in (("RDF_TRIPLET", legacy_directory), ("RDF_TRIPLET_OPEN_AI", open_ai_directory)):
        if directory and str(directory).strip() and root == resolve_project_path(directory):
            raise ValueError(f"RDF_TRIPLET_OPEN_ROUTER must differ from {name}")


def create_open_router_llm_client(settings, **client_options):
    options = {"timeout": 60.0, "max_retries": 2}
    options.update(client_options)
    options.update(base_url=OPENROUTER_API_BASE, api_key=require_open_router_key(settings))
    return AsyncOpenAI(**options)


def open_router_llm_completion_options(settings):
    return {
        "model": open_router_model_name(settings),
        "temperature": 0.0,
        "extra_body": {
            "reasoning": {"enabled": False},
            "provider": {"require_parameters": True},
        },
    }


def redact_open_router_error(error, *, api_key):
    """Keep stack frames while removing credentials and the original exception chain."""
    if not api_key:
        return error
    return RuntimeError(str(error).replace(api_key, "[REDACTED]")).with_traceback(error.__traceback__)
