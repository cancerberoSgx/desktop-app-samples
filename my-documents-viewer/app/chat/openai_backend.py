import json
import urllib.error
import urllib.request
from typing import Dict, List

from .base import ChatBackend, ChatError

API_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 60


class OpenAIChatBackend(ChatBackend):
    """OpenAI chat completions via a plain HTTPS call - no `openai` SDK
    dependency, same reasoning as OpenAIEmbeddingBackend. Requires an API
    key, set per-profile (shared with the profile's embedding config, if
    any - same provider)."""

    def __init__(self, model_name: str, api_key: str) -> None:
        if not api_key:
            raise ChatError(
                "This profile has no OpenAI API key set - add one on the Profiles screen."
            )
        self._model_name = model_name
        self._api_key = api_key

    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 800) -> str:
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(
                {"model": self._model_name, "messages": messages, "max_tokens": max_tokens}
            ).encode("utf-8"),
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
            raise ChatError(f"OpenAI chat request failed ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise ChatError(f"Could not reach OpenAI: {exc.reason}") from exc

        return payload["choices"][0]["message"]["content"]
