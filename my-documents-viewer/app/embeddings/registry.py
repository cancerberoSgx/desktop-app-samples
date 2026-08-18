from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class EmbeddingModelInfo:
    backend: str  # 'fastembed' | 'openai' | 'gemini'
    model_name: str
    display_name: str
    dimension: int
    requires_api_key: bool = False


# The catalog of embedding models offered in the Profile dialog. fastembed
# models run locally (no API key, no per-call cost); openai/gemini models
# require the corresponding API key to be set on the profile. Adding a model
# here is enough to offer it in the UI - ProfileDialog and
# embeddings.get_backend() both read the model's dimension/backend from this
# table rather than hardcoding it a second time.
EMBEDDING_MODELS: List[EmbeddingModelInfo] = [
    EmbeddingModelInfo(
        "fastembed", "BAAI/bge-small-en-v1.5",
        "FastEmbed - bge-small-en-v1.5 (384d, local, default)", 384,
    ),
    EmbeddingModelInfo(
        "fastembed", "BAAI/bge-base-en-v1.5",
        "FastEmbed - bge-base-en-v1.5 (768d, local, higher quality)", 768,
    ),
    EmbeddingModelInfo(
        "fastembed", "sentence-transformers/all-MiniLM-L6-v2",
        "FastEmbed - all-MiniLM-L6-v2 (384d, local, fast/general)", 384,
    ),
    EmbeddingModelInfo(
        "openai", "text-embedding-3-small",
        "OpenAI - text-embedding-3-small (1536d, API key)", 1536, requires_api_key=True,
    ),
    EmbeddingModelInfo(
        "openai", "text-embedding-3-large",
        "OpenAI - text-embedding-3-large (3072d, API key)", 3072, requires_api_key=True,
    ),
    EmbeddingModelInfo(
        "gemini", "text-embedding-004",
        "Gemini - text-embedding-004 (768d, API key)", 768, requires_api_key=True,
    ),
]

DEFAULT_MODEL = EMBEDDING_MODELS[0]

BACKEND_LABELS = [
    ("fastembed", "FastEmbed (local, no API key)"),
    ("openai", "OpenAI (API key required)"),
    ("gemini", "Gemini (API key required)"),
]


def models_for_backend(backend: str) -> List[EmbeddingModelInfo]:
    return [model for model in EMBEDDING_MODELS if model.backend == backend]


def find_model(backend: str, model_name: str) -> Optional[EmbeddingModelInfo]:
    return next(
        (model for model in EMBEDDING_MODELS if model.backend == backend and model.model_name == model_name),
        None,
    )


def dimension_for(backend: str, model_name: str) -> int:
    model = find_model(backend, model_name)
    if model is None:
        raise ValueError(f"Unknown embedding model: {backend}/{model_name}")
    return model.dimension
