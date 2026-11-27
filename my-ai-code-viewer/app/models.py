"""Plain data shapes shared by providers, repositories and the UI.

Providers (app/providers/*) produce `ConversationSource` + `ParsedConversation`
objects; the indexer persists them; the UI reads back `Project`,
`Conversation` and `MessageBlock` rows from the app's own SQLite database.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MessageBlock:
    role: str                     # 'user' | 'assistant' | 'system'
    kind: str                     # 'text' | 'thinking' | 'tool_use' | 'tool_result' | 'error'
    text: str = ""
    tool_name: Optional[str] = None
    timestamp: Optional[str] = None
    is_error: bool = False
    seq: int = 0
    id: Optional[int] = None


@dataclass
class ConversationSource:
    """Cheap-to-list handle on one conversation in a provider - enough to
    decide whether it changed since the last sync without parsing it."""
    provider: str
    external_id: str
    project_key: str
    project_path: str
    source_path: str
    fingerprint: str
    parent_external_id: Optional[str] = None
    title_hint: Optional[str] = None


@dataclass
class ParsedConversation:
    external_id: str
    project_path: Optional[str] = None   # refined project path, if the content reveals it
    parent_external_id: Optional[str] = None
    title: Optional[str] = None
    first_prompt: Optional[str] = None
    model: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Optional[float] = None
    tools_used: Dict[str, int] = field(default_factory=dict)
    blocks: List[MessageBlock] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.blocks)

    @property
    def user_message_count(self) -> int:
        return sum(1 for b in self.blocks if b.role == "user" and b.kind == "text")

    @property
    def tool_call_count(self) -> int:
        return sum(1 for b in self.blocks if b.kind == "tool_use")


@dataclass
class Project:
    id: int
    provider: str
    project_key: str
    path: str
    name: str
    last_activity: Optional[str]
    conversation_count: int = 0
    total_tokens: int = 0
    cost_usd: Optional[float] = None  # sum of known costs; None if none is known
    cost_unknown: int = 0  # top-level conversations without a recorded cost


@dataclass
class ProjectGroup:
    """One folder as shown in the UI: every provider's project rows that
    share the same path, merged (so a folder used with both Claude Code and
    OpenCode is a single entry listing both tools' conversations)."""
    path: str
    name: str
    providers: List[str]
    project_ids: List[int]
    conversation_count: int
    last_activity: Optional[str]
    total_tokens: int = 0
    cost_usd: Optional[float] = None
    cost_unknown: int = 0


@dataclass
class Conversation:
    id: int
    project_id: int
    provider: str
    external_id: str
    parent_external_id: Optional[str]
    title: Optional[str]
    first_prompt: Optional[str]
    model: Optional[str]
    cwd: Optional[str]
    git_branch: Optional[str]
    started_at: Optional[str]
    updated_at: Optional[str]
    message_count: int
    user_message_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Optional[float]
    tools_used: Dict[str, int]
    source_path: Optional[str]
    missing_from_source: bool

    @property
    def display_title(self) -> str:
        text = (self.title or self.first_prompt or "").strip()
        return text.splitlines()[0][:200] if text else self.external_id
