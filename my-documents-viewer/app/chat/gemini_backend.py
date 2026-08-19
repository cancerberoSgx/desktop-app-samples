import json
import urllib.error
import urllib.request
from typing import Dict, List

from .base import ChatBackend, ChatError

API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
REQUEST_TIMEOUT_SECONDS = 60

_ROLE_TO_GEMINI = {"user": "user", "assistant": "model"}


class GeminiChatBackend(ChatBackend):
    """Gemini chat completions via a plain HTTPS call to the
    `generateContent` endpoint - no `google-generativeai` SDK dependency,
    same reasoning as GeminiEmbeddingBackend. Requires an API key, set
    per-profile (shared with the profile's embedding config, if any - same
    provider).

    Gemini's request shape differs from the flat OpenAI-style messages list
    ChatBackend.complete() takes: a leading run of role="system" messages
    becomes `systemInstruction`, and the rest become `contents`, with
    "assistant" renamed to Gemini's "model" role.
    """

    def __init__(self, model_name: str, api_key: str) -> None:
        if not api_key:
            raise ChatError(
                "This profile has no Gemini API key set - add one on the Profiles screen."
            )
        self._model_name = model_name
        self._api_key = api_key

    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 800) -> str:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = [
            {"role": _ROLE_TO_GEMINI.get(m["role"], "user"), "parts": [{"text": m["content"]}]}
            for m in messages
            if m["role"] != "system"
        ]

        body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        url = API_URL_TEMPLATE.format(model=self._model_name, key=self._api_key)
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
            body_text = exc.read().decode("utf-8", errors="replace")
            raise ChatError(f"Gemini chat request failed ({exc.code}): {body_text}") from exc
        except urllib.error.URLError as exc:
            raise ChatError(f"Could not reach Gemini: {exc.reason}") from exc

        try:
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ChatError(f"Gemini returned an unexpected response: {payload}") from exc
