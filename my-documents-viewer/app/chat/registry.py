from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ChatModelInfo:
    backend: str  # 'openai' | 'gemini'
    model_name: str
    display_name: str
    context_window: int
    requires_api_key: bool = True


# The catalog of chat models offered in the Profile dialog's Chat section -
# same shape/purpose as embeddings.registry.EMBEDDING_MODELS, but for
# text generation rather than embedding. No 'fastembed' entry: it's a local
# embedding-only model with no text-generation capability, so chat is
# openai/gemini-only and always requires an API key.
CHAT_MODELS: List[ChatModelInfo] = [
    ChatModelInfo("openai", "gpt-4o-mini", "OpenAI - gpt-4o-mini (fast, low cost)", 128_000),
    ChatModelInfo("openai", "gpt-4o", "OpenAI - gpt-4o (higher quality)", 128_000),
    ChatModelInfo("gemini", "gemini-1.5-flash", "Gemini - gemini-1.5-flash (fast, low cost)", 1_000_000),
    ChatModelInfo("gemini", "gemini-1.5-pro", "Gemini - gemini-1.5-pro (higher quality)", 2_000_000),
]

CHAT_BACKEND_LABELS = [
    ("openai", "OpenAI (API key required)"),
    ("gemini", "Gemini (API key required)"),
]


def models_for_backend(backend: str) -> List[ChatModelInfo]:
    return [model for model in CHAT_MODELS if model.backend == backend]


def find_model(backend: str, model_name: str) -> Optional[ChatModelInfo]:
    return next(
        (model for model in CHAT_MODELS if model.backend == backend and model.model_name == model_name),
        None,
    )
