from typing import List

from .base import EmbeddingBackend, EmbeddingError


class FastEmbedBackend(EmbeddingBackend):
    """Local, CPU-only embeddings via the `fastembed` package (ONNX Runtime
    under the hood - no PyTorch dependency). Default backend, needs no API
    key. The model file (tens to a couple hundred MB) is downloaded on first
    use and cached under app/db/paths.fastembed_cache_dir()."""

    def __init__(self, model_name: str, dimension: int) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._model = None  # lazy: importing fastembed/onnxruntime and
        # downloading the model is slow - don't pay that cost until embed()
        # is actually called.

    @property
    def dimension(self) -> int:
        return self._dimension

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingError(
                "fastembed is not installed - run `pip install fastembed`."
            ) from exc

        from ..db.paths import fastembed_cache_dir

        try:
            self._model = TextEmbedding(model_name=self._model_name, cache_dir=str(fastembed_cache_dir()))
        except Exception as exc:  # noqa: BLE001 - surfaced as EmbeddingError to the UI
            raise EmbeddingError(f"Could not load fastembed model {self._model_name!r}: {exc}") from exc
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        try:
            return [vector.tolist() for vector in model.embed(texts)]
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"fastembed failed to embed text: {exc}") from exc
