from .base import EmbeddingBackend, EmbeddingError
from .registry import EMBEDDING_MODELS, EmbeddingModelInfo, dimension_for, find_model, models_for_backend


def get_backend(profile) -> EmbeddingBackend:
    """Build the EmbeddingBackend a profile's stored config describes.
    Imported lazily inside each backend module (not at package import time)
    so e.g. an OpenAI-only install never has to import fastembed/onnxruntime
    just to embed a search query."""
    backend = profile.embedding_backend

    if backend == "fastembed":
        from .fastembed_backend import FastEmbedBackend

        return FastEmbedBackend(profile.embedding_model, profile.embedding_dim)

    if backend == "openai":
        from .openai_backend import OpenAIEmbeddingBackend

        return OpenAIEmbeddingBackend(profile.embedding_model, profile.embedding_dim, profile.openai_api_key)

    if backend == "gemini":
        from .gemini_backend import GeminiEmbeddingBackend

        return GeminiEmbeddingBackend(profile.embedding_model, profile.embedding_dim, profile.gemini_api_key)

    raise EmbeddingError(f"Unknown embedding backend: {backend!r}")


__all__ = [
    "EmbeddingBackend",
    "EmbeddingError",
    "EMBEDDING_MODELS",
    "EmbeddingModelInfo",
    "dimension_for",
    "find_model",
    "models_for_backend",
    "get_backend",
]
