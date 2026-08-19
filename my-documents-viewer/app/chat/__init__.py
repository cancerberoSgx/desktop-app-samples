from .base import ChatBackend, ChatError
from .registry import CHAT_BACKEND_LABELS, CHAT_MODELS, ChatModelInfo, find_model, models_for_backend


def get_chat_backend(profile) -> ChatBackend:
    """Build the ChatBackend a profile's stored chat config describes.
    Imported lazily inside each backend module (not at package import time),
    same reasoning as embeddings.get_backend. Raises ChatError if the
    profile has no chat backend configured yet - callers (ChatPage,
    ChatService) show that as "configure a chat model on the Profiles
    screen" rather than crashing."""
    backend = profile.chat_backend

    if not backend:
        raise ChatError("No chat model configured for this profile - set one on the Profiles screen.")

    if backend == "openai":
        from .openai_backend import OpenAIChatBackend

        return OpenAIChatBackend(profile.chat_model, profile.openai_api_key)

    if backend == "gemini":
        from .gemini_backend import GeminiChatBackend

        return GeminiChatBackend(profile.chat_model, profile.gemini_api_key)

    raise ChatError(f"Unknown chat backend: {backend!r}")


__all__ = [
    "ChatBackend",
    "ChatError",
    "CHAT_MODELS",
    "CHAT_BACKEND_LABELS",
    "ChatModelInfo",
    "find_model",
    "models_for_backend",
    "get_chat_backend",
]
