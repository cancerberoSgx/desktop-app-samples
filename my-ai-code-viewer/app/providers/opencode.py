"""OpenCode: a single SQLite database (~/.local/share/opencode/opencode.db)
with `session`, `message` and `part` tables; message/part payloads are JSON
in their `data` column. Opened strictly read-only.

Projects are grouped by session.directory (the folder OpenCode was launched
in) rather than OpenCode's own `project` table, which is keyed by git
worktree root - that keeps grouping consistent with Claude Code's
per-launch-directory folders.
"""
import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from app.models import ConversationSource, MessageBlock, ParsedConversation
from app.providers._util import clip, ms_to_iso, to_text
from app.providers.base import ProviderAdapter

PROVIDER_ID = "opencode"
DEFAULT_TITLE_PREFIX = "New session - "


def default_db_path() -> Path:
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


class OpenCodeProvider(ProviderAdapter):
    id = PROVIDER_ID
    label = "OpenCode"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()

    def is_available(self) -> bool:
        return self.db_path.is_file()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def list_sources(self) -> List[ConversationSource]:
        if not self.is_available():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, parent_id, directory, title, time_updated FROM session ORDER BY time_created"
            ).fetchall()
        finally:
            conn.close()
        return [
            ConversationSource(
                provider=self.id, external_id=r["id"], project_key=r["directory"],
                project_path=r["directory"], source_path=str(self.db_path),
                fingerprint=str(r["time_updated"]), parent_external_id=r["parent_id"] or None,
                title_hint=r["title"],
            )
            for r in rows
        ]

    def parse(self, source: ConversationSource) -> ParsedConversation:
        conn = self._connect()
        try:
            return self._parse(conn, source)
        finally:
            conn.close()

    def _parse(self, conn: sqlite3.Connection, source: ConversationSource) -> ParsedConversation:
        s = conn.execute("SELECT * FROM session WHERE id = ?", (source.external_id,)).fetchone()
        conv = ParsedConversation(external_id=source.external_id, project_path=source.project_path,
                                  parent_external_id=source.parent_external_id)
        if s is None:
            return conv
        title = s["title"] or ""
        conv.title = None if title.startswith(DEFAULT_TITLE_PREFIX) else title
        conv.cwd = s["directory"]
        conv.started_at = ms_to_iso(s["time_created"])
        conv.updated_at = ms_to_iso(s["time_updated"])
        conv.cost_usd = s["cost"]
        conv.input_tokens = s["tokens_input"] or 0
        conv.output_tokens = (s["tokens_output"] or 0) + (s["tokens_reasoning"] or 0)
        conv.cache_read_tokens = s["tokens_cache_read"] or 0
        conv.cache_write_tokens = s["tokens_cache_write"] or 0
        conv.model = _model_name(s["model"])

        parts_by_message = {}
        for p in conn.execute(
            "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY message_id, id",
            (source.external_id,),
        ):
            parts_by_message.setdefault(p["message_id"], []).append(p["data"])

        for m in conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created, id",
            (source.external_id,),
        ):
            data = _loads(m["data"])
            role = data.get("role") or "assistant"
            ts = ms_to_iso(m["time_created"])
            if data.get("modelID"):
                conv.model = data["modelID"]
            for raw in parts_by_message.get(m["id"], []):
                _append_part(conv, role, _loads(raw), ts)
            error = data.get("error")
            if error:
                msg = (error.get("data") or {}).get("message") if isinstance(error, dict) else None
                name = error.get("name") if isinstance(error, dict) else None
                text = ": ".join(x for x in (name, msg) if x) or to_text(error)
                conv.blocks.append(MessageBlock(role, "error", clip(text), timestamp=ts, is_error=True))

        for seq, block in enumerate(conv.blocks):
            block.seq = seq
        return conv


def _loads(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _model_name(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    data = _loads(raw)
    return data.get("id") or raw


def _append_part(conv: ParsedConversation, role: str, part: dict, ts: Optional[str]) -> None:
    t = part.get("type")
    if t == "text":
        text = part.get("text", "")
        if text.strip():
            conv.blocks.append(MessageBlock(role, "text", clip(text), timestamp=ts))
            if role == "user" and conv.first_prompt is None:
                conv.first_prompt = text.strip()[:500]
    elif t == "reasoning":
        text = part.get("text", "")
        if text.strip():
            conv.blocks.append(MessageBlock(role, "thinking", clip(text), timestamp=ts))
    elif t == "tool":
        name = part.get("tool") or "tool"
        state = part.get("state") or {}
        conv.tools_used[name] = conv.tools_used.get(name, 0) + 1
        conv.blocks.append(MessageBlock(role, "tool_use", clip(to_text(state.get("input"))),
                                        tool_name=name, timestamp=ts))
        status = state.get("status")
        if status == "error":
            conv.blocks.append(MessageBlock(role, "tool_result", clip(to_text(state.get("error"))),
                                            tool_name=name, timestamp=ts, is_error=True))
        elif "output" in state:
            conv.blocks.append(MessageBlock(role, "tool_result", clip(to_text(state.get("output"))),
                                            tool_name=name, timestamp=ts))
    elif t == "file":
        conv.blocks.append(MessageBlock(role, "text", f"[file: {part.get('filename') or part.get('url') or ''}]",
                                        timestamp=ts))
    elif t == "compaction":
        conv.blocks.append(MessageBlock("system", "text", "[context compacted]", timestamp=ts))
