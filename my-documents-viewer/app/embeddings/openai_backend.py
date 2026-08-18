import json
import urllib.error
import urllib.request
from typing import List

from .base import EmbeddingBackend, EmbeddingError

API_URL = "https://api.openai.com/v1/embeddings"
REQUEST_TIMEOUT_SECONDS = 60
# OpenAI's embeddings endpoint accepts multiple inputs per call but caps the
# array size - keep each request to a fixed, well-under-the-limit batch
# regardless of how many chunks a document produced.
BATCH_SIZE = 100


class OpenAIEmbeddingBackend(EmbeddingBackend):
    """OpenAI embeddings via a plain HTTPS call to the Embeddings API - no
    `openai` SDK dependency, since this is an optional backend and the raw
    REST call is a handful of lines. Requires an API key, set per-profile."""

    def __init__(self, model_name: str, dimension: int, api_key: str) -> None:
        if not api_key:
            raise EmbeddingError(
                "This profile has no OpenAI API key set - add one on the Profiles screen."
            )
        self._model_name = model_name
        self._dimension = dimension
        self._api_key = api_key

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        vectors: List[List[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self._embed_batch(texts[start:start + BATCH_SIZE]))
        return vectors

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        request = urllib.request.Request(
            API_URL,
            data=json.dumps({"model": self._model_name, "input": batch}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise EmbeddingError(f"OpenAI embeddings request failed ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise EmbeddingError(f"Could not reach OpenAI: {exc.reason}") from exc

        # The API returns items in the order requested, but doesn't
        # guarantee it - `index` says where each one belongs.
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]
