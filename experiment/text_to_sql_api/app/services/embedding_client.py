import numpy as np

from app.core.config import resolve_embedding_api_base


class EmbeddingAPIError(RuntimeError):
    """The embedding server failed or returned invalid vectors."""


def create_embedding_client(settings, **client_options):
    """Use the reference project's OpenAI-compatible raw-text embedding client."""
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.embedding_model_name,
        base_url=resolve_embedding_api_base(settings.embedding_api_base),
        api_key="dummy",
        check_embedding_ctx_length=False,
        chunk_size=32,
        request_timeout=60,
        max_retries=2,
        **client_options,
    )


def normalize_embedding_vectors(values, expected_count: int) -> np.ndarray:
    vectors = np.asarray(values, dtype=np.float64)
    if vectors.ndim != 2 or vectors.shape[0] != expected_count or vectors.shape[1] == 0:
        raise ValueError("Embedding API must return one equal-length vector per input")
    if not np.isfinite(vectors).all():
        raise ValueError("Embedding vectors must contain finite numbers")
    with np.errstate(over="ignore", invalid="ignore"):
        norms = np.linalg.norm(vectors, axis=1)
    if not np.isfinite(norms).all() or (norms <= 0).any():
        raise ValueError("Embedding vectors must have finite nonzero norms")
    return np.asarray(vectors / norms[:, None], dtype=np.float32, order="C")
