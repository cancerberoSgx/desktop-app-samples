from abc import ABC, abstractmethod
from typing import List


class EmbeddingError(Exception):
    """Raised when an embedding backend can't produce vectors: missing
    optional dependency (fastembed/sqlite-vec not installed), missing/
    invalid API key, or a network/API failure. Callers (repositories,
    pages) are expected to catch this and show it as a message box rather
    than let it crash the app - embedding failures are always recoverable
    (fix the key, check the network, pick a different backend)."""


class EmbeddingBackend(ABC):
    """One embedding provider, bound to a specific model. Implementations:
    FastEmbedBackend (local, ONNX, no API key), OpenAIEmbeddingBackend,
    GeminiEmbeddingBackend (both plain HTTPS calls - no SDK dependency)."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector length this backend/model produces - must match the
        `embedding_dim` stored on the profile, which is what sizes the
        profile's sqlite-vec `vec0` table (see
        repositories.DocumentRepository._ensure_vec_table)."""

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts, returning one vector per input text, in
        the same order. Must accept an empty list and return an empty list."""
