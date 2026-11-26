import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_PORT = 31065
DEFAULT_LLM_MODEL = "DeepSeek-V4-Flash-0731"
OPENAI_API_BASE = "https://api.openai.com/v1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_LLM_MODEL = "gpt-6-luna"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_LLM_MODEL = "deepseek/deepseek-v4-flash-0731"


def _environment(name: str, default: str = "") -> str:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return os.getenv(name, default).strip()


def _server_port(value: str | int) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError
        port = int(value)
    except ValueError:
        raise ValueError("API_PORT or --port must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("API_PORT or --port must be an integer between 1 and 65535")
    return port


def _argument_port(value: str) -> int:
    try:
        return _server_port(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def resolve_project_path(value: str | Path) -> Path:
    if not str(value).strip():
        raise ValueError("A nonempty storage or dataset path is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def resolve_embedding_api_base(value: str) -> str:
    api_base = value.strip().rstrip("/")
    parts = urlsplit(api_base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("EMBEDDING_API_BASE must be an HTTP(S) base URL")
    return api_base


def resolve_llm_api_base(value: str) -> str:
    api_base = value.strip().rstrip("/")
    parts = urlsplit(api_base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("LLM_API_BASE must be an HTTP(S) base URL")
    return api_base


def resolve_llm_model_name(value: str) -> str:
    model_name = value.strip().removeprefix("openai/")
    if not model_name.strip():
        raise ValueError("LLM_MODEL_NAME or --model_name must specify a model")
    return model_name


@dataclass
class AppSettings:
    host: str = "0.0.0.0"
    port: int = field(default_factory=lambda: _server_port(_environment("API_PORT", str(DEFAULT_API_PORT))))
    dataset_path: str = "./dataset/db_schema.json"
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_api_base: str = field(default_factory=lambda: _environment("EMBEDDING_API_BASE"))
    embedded_schema: str = field(default_factory=lambda: _environment("EMBEDDED_SCHEMA", "./embedded_schema"))
    embedded_triplet: str = field(default_factory=lambda: _environment("EMBEDDED_TRIPLET", "./embedded_triplet"))
    embedded_triplet_open_ai: str = field(
        default_factory=lambda: _environment("EMBEDDED_TRIPLET_OPEN_AI", "./embedded_triplet_open_ai"),
    )
    llm_api_base: str = field(default_factory=lambda: _environment("LLM_API_BASE"))
    model_name: str = field(default_factory=lambda: _environment("LLM_MODEL_NAME", DEFAULT_LLM_MODEL))
    openai_api_key: str = field(default_factory=lambda: _environment("OPENAI_API_KEY"), repr=False)
    openai_embedding_model_name: str = field(
        default_factory=lambda: _environment("OPENAI_EMBEDDING_MODEL_NAME", DEFAULT_OPENAI_EMBEDDING_MODEL),
    )
    openai_llm_model_name: str = field(
        default_factory=lambda: _environment("OPENAI_LLM_MODEL_NAME", DEFAULT_OPENAI_LLM_MODEL),
    )
    embedded_schema_open_ai: str = field(
        default_factory=lambda: _environment("EMBEDDED_SCHEMA_OPEN_AI", "./embedded_schema_open_ai"),
    )
    rdf_triplet: str = field(default_factory=lambda: _environment("RDF_TRIPLET", "./rdf_triplet"))
    rdf_triplet_open_ai: str = field(
        default_factory=lambda: _environment("RDF_TRIPLET_OPEN_AI", "./rdf_triplet_open_ai"),
    )
    openrouter_api_key: str = field(default_factory=lambda: _environment("OPENROUTER_API_KEY"), repr=False)
    openrouter_llm_model_name: str = field(
        default_factory=lambda: _environment("OPENROUTER_LLM_MODEL_NAME", DEFAULT_OPENROUTER_LLM_MODEL),
    )
    rdf_triplet_open_router: str = field(
        default_factory=lambda: _environment("RDF_TRIPLET_OPEN_ROUTER", "./rdf_triplet_open_router"),
    )

    def __post_init__(self):
        self.port = _server_port(self.port)


def get_default_settings() -> AppSettings:
    return AppSettings()


def parse_args(argv=None) -> AppSettings:
    # Defer environment-port validation until argparse knows whether --port overrides it.
    defaults = AppSettings(port=DEFAULT_API_PORT)
    parser = argparse.ArgumentParser(description="Text-to-SQL indexing, retrieval, triplet and SQL generation API")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", default=_environment("API_PORT", str(DEFAULT_API_PORT)), type=_argument_port)
    parser.add_argument("--dataset_path", default=defaults.dataset_path)
    parser.add_argument("--embedding_model_name", default=defaults.embedding_model_name)
    parser.add_argument("--llm_api_base", default=defaults.llm_api_base)
    parser.add_argument("--model_name", default=defaults.model_name)
    return AppSettings(**vars(parser.parse_args(argv)))
