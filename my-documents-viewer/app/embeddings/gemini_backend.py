import json
import urllib.error
import urllib.request
from typing import List

from .base import EmbeddingBackend, EmbeddingError

API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents?key={key}"
)
REQUEST_TIMEOUT_SECONDS = 60
# Gemini's batchEmbedContents caps how many requests can go in one call.
BATCH_SIZE = 100


class GeminiEmbeddingBackend(EmbeddingBackend):
    """Gemini embeddings via a plain HTTPS call to the
    `batchEmbedContents` endpoint - no `google-generativeai` SDK dependency,
    same reasoning as OpenAIEmbeddingBackend. Requires an API key, set
    per-profile."""

    def __init__(self, model_name: str, dimension: int, api_key: str) -> None:
        if not api_key:
            raise EmbeddingError(
                "This profile has no Gemini API key set - add one on the Profiles screen."
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

        model_path = f"models/{self._model_name}"
        url = API_URL_TEMPLATE.format(model=self._model_name, key=self._api_key)

        vectors: List[List[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]
            body = {
                "requests": [
                    {"model": model_path, "content": {"parts": [{"text": text}]}}
                    for text in batch
                ]
            }
            request = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise EmbeddingError(f"Gemini embeddings request failed ({exc.code}): {error_body}") from exc
            except urllib.error.URLError as exc:
                raise EmbeddingError(f"Could not reach Gemini: {exc.reason}") from exc

            vectors.extend(item["values"] for item in payload["embeddings"])
        return vectors
