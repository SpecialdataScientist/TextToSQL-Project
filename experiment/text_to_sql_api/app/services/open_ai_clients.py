from openai import AsyncOpenAI

from app.core.config import OPENAI_API_BASE, resolve_project_path


def require_open_ai_key(settings):
    key = settings.openai_api_key.strip()
    if not key:
        raise ValueError("OPENAI_API_KEY must be set in .env or the environment")
    return key


def _require_model(value, field_name):
    model = value.strip().removeprefix("openai/")
    if not model.strip():
        raise ValueError(f"{field_name} must specify a model")
    return model


def validate_open_ai_embedding_settings(settings, *, legacy_directory):
    require_open_ai_key(settings)
    _require_model(settings.embedding_model_name, "OPENAI_EMBEDDING_MODEL_NAME")
    root = resolve_project_path(settings.embedded_schema)
    if root == resolve_project_path(legacy_directory):
        raise ValueError("EMBEDDED_SCHEMA_OPEN_AI must differ from EMBEDDED_SCHEMA")


def validate_open_ai_triplet_embedding_settings(settings, *, legacy_directory):
    require_open_ai_key(settings)
    _require_model(settings.embedding_model_name, "OPENAI_EMBEDDING_MODEL_NAME")
    root = resolve_project_path(settings.embedded_triplet)
    if root == resolve_project_path(legacy_directory):
        raise ValueError("EMBEDDED_TRIPLET_OPEN_AI must differ from EMBEDDED_TRIPLET")


def validate_open_ai_hybrid_settings(settings, *, legacy_embedding_directory, legacy_rdf_directory):
    validate_open_ai_triplet_embedding_settings(settings, legacy_directory=legacy_embedding_directory)
    root = resolve_project_path(settings.rdf_triplet)
    if root == resolve_project_path(legacy_rdf_directory):
        raise ValueError("RDF_TRIPLET_OPEN_AI must differ from RDF_TRIPLET")


def validate_open_ai_llm_settings(settings):
    require_open_ai_key(settings)
    _require_model(settings.model_name, "OPENAI_LLM_MODEL_NAME")


def validate_open_ai_triplet_settings(settings, *, legacy_directory):
    validate_open_ai_llm_settings(settings)
    root = resolve_project_path(settings.rdf_triplet)
    if root == resolve_project_path(legacy_directory):
        raise ValueError("RDF_TRIPLET_OPEN_AI must differ from RDF_TRIPLET")


def create_open_ai_embedding_client(settings, **client_options):
    from langchain_openai import OpenAIEmbeddings

    options = {
        "check_embedding_ctx_length": False,
        "chunk_size": 32,
        "request_timeout": 60,
        "max_retries": 2,
    }
    options.update(client_options)
    options.update(
        model=_require_model(settings.embedding_model_name, "OPENAI_EMBEDDING_MODEL_NAME"),
        base_url=OPENAI_API_BASE,
        api_key=require_open_ai_key(settings),
    )
    return OpenAIEmbeddings(**options)


def create_open_ai_llm_client(settings, **client_options):
    options = {"timeout": 60.0, "max_retries": 2}
    options.update(client_options)
    options.update(base_url=OPENAI_API_BASE, api_key=require_open_ai_key(settings))
    return AsyncOpenAI(**options)


def open_ai_llm_completion_options(settings):
    model = _require_model(settings.model_name, "OPENAI_LLM_MODEL_NAME")
    options = {"model": model}
    if model.startswith("gpt-6-luna"):
        options["reasoning_effort"] = "none"
    return options


def redact_open_ai_error(error, *, api_key):
    """Keep useful stack frames without exposing a key in exception chains."""
    if not api_key:
        return error
    message = str(error).replace(api_key, "[REDACTED]")
    return RuntimeError(message).with_traceback(error.__traceback__)
