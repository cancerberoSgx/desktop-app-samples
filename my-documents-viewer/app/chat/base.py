from abc import ABC, abstractmethod
from typing import Dict, List


class ChatError(Exception):
    """Raised when a chat backend can't produce a reply: missing/invalid API
    key, network/API failure, or no chat model configured for the profile
    (see get_chat_backend). Mirrors embeddings.EmbeddingError - callers only
    ever need to catch this one exception type, never a provider-specific
    one."""


class ChatBackend(ABC):
    """One text-generation provider, bound to a specific model. Used by
    ChatService (app/chat_service.py) both to condense a follow-up question
    into a standalone search query and to generate the final answer -
    implementations: OpenAIChatBackend, GeminiChatBackend (plain HTTPS calls,
    no SDK dependency - same reasoning as app/embeddings/*_backend.py).
    There is no fastembed chat backend: fastembed is a local embedding-only
    model with no text-generation capability."""

    @abstractmethod
    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 800) -> str:
        """`messages` is an ordered list of {"role": "system"|"user"|"assistant",
        "content": str} dicts - the same shape regardless of backend; each
        implementation adapts it to its own API's request format. Returns the
        model's reply text."""
