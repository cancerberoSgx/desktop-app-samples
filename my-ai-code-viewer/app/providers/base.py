from abc import ABC, abstractmethod
from typing import List

from app.models import ConversationSource, ParsedConversation


class ProviderAdapter(ABC):
    """One AI coding CLI (Claude Code, OpenCode, ...) as seen by the indexer.

    Adding a new tool = one new subclass registered in
    app/providers/__init__.py::build_providers - nothing in the UI or the
    database schema is provider-specific.
    """

    #: Stable id stored in the database (projects.provider / conversations.provider).
    id: str = ""
    #: Human label shown in the UI.
    label: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this tool's data exists on this machine at all."""

    @abstractmethod
    def list_sources(self) -> List[ConversationSource]:
        """Every conversation currently on disk - must be cheap (no full
        parsing), since it runs on every sync to detect changes."""

    @abstractmethod
    def parse(self, source: ConversationSource) -> ParsedConversation:
        """Fully load one conversation into normalized blocks."""
