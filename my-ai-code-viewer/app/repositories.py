import json
import sqlite3
from typing import Dict, Iterable, List, Optional

from app.models import Conversation, MessageBlock, ParsedConversation, Project, ProjectGroup

CLAUDE_PROJECTS_DIR_SETTING_KEY = "claude_projects_dir"
OPENCODE_DB_PATH_SETTING_KEY = "opencode_db_path"
CODEX_HOME_SETTING_KEY = "codex_home"
GEMINI_HOME_SETTING_KEY = "gemini_home"
SHOW_TOOLS_SETTING_KEY = "show_tool_calls"
SHOW_THINKING_SETTING_KEY = "show_thinking"
RENDER_MARKDOWN_SETTING_KEY = "render_markdown"
QA_ONLY_SETTING_KEY = "qa_only"
SHOW_OUTLINE_SETTING_KEY = "show_outline"
OUTLINE_SASH_SETTING_KEY = "outline_sash"
EXPORT_DIR_SETTING_KEY = "export_dir"
LAST_PROJECT_SETTING_KEY = "last_project_id"
LAST_CONVERSATION_SETTING_KEY = "last_conversation_id"
PROVIDER_FILTER_SETTING_KEY = "provider_filter"


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite) - backs both the
    Preferences dialog and the "restore where I left off" bits (sash positions,
    last project/conversation, viewer toggles)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: Optional[str]) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        return default if value is None else value == "1"

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, "1" if value else "0")

    def get_int(self, key: str) -> Optional[int]:
        value = self.get(key)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    def set_int(self, key: str, value: Optional[int]) -> None:
        self.set(key, str(value) if value is not None else None)



def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    try:
        tools = json.loads(row["tools_used"]) if row["tools_used"] else {}
    except ValueError:
        tools = {}
    return Conversation(
        id=row["id"], project_id=row["project_id"], provider=row["provider"],
        external_id=row["external_id"], parent_external_id=row["parent_external_id"],
        title=row["title"], first_prompt=row["first_prompt"], model=row["model"],
        cwd=row["cwd"], git_branch=row["git_branch"], started_at=row["started_at"],
        updated_at=row["updated_at"], message_count=row["message_count"],
        user_message_count=row["user_message_count"], tool_call_count=row["tool_call_count"],
        input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"], cache_write_tokens=row["cache_write_tokens"],
        cost_usd=row["cost_usd"], tools_used=tools, source_path=row["source_path"],
        missing_from_source=bool(row["missing_from_source"]),
    )


class IndexRepository:
    """The local index/archive of projects, conversations and their
    messages (pure SQL against SQLite). Written by app/indexer.py, read by
    the UI."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # -------------------------------------------------------------- projects
    def upsert_project(self, provider: str, project_key: str, path: str) -> int:
        name = path.rstrip("/").rsplit("/", 1)[-1] or path
        self._conn.execute(
            "INSERT INTO projects (provider, project_key, path, name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(provider, project_key) DO NOTHING",
            (provider, project_key, path, name),
        )
        row = self._conn.execute(
            "SELECT id FROM projects WHERE provider = ? AND project_key = ?", (provider, project_key)
        ).fetchone()
        return row["id"]

    def set_project_path(self, project_id: int, path: str) -> None:
        name = path.rstrip("/").rsplit("/", 1)[-1] or path
        self._conn.execute("UPDATE projects SET path = ?, name = ? WHERE id = ?", (path, name, project_id))

    def refresh_project_activity(self) -> None:
        self._conn.execute(
            "UPDATE projects SET last_activity = "
            "(SELECT MAX(updated_at) FROM conversations c WHERE c.project_id = projects.id)"
        )

    def list_projects(self, provider: Optional[str] = None, text_filter: str = "") -> List[Project]:
        sql = (
            "SELECT p.*, COALESCE(t.conversation_count, 0) AS conversation_count, "
            "COALESCE(t.total_tokens, 0) AS total_tokens, t.cost_usd, COALESCE(t.cost_unknown, 0) AS cost_unknown "
            "FROM projects p LEFT JOIN ("
            # Subagents count toward tokens/cost (real spend) but not the
            # conversation count; a missing cost is only flagged for top-level
            # conversations (Claude subagents never carry one) - as in stats.
            "SELECT project_id, SUM(parent_external_id IS NULL) AS conversation_count, "
            "SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens) AS total_tokens, "
            "SUM(cost_usd) AS cost_usd, "
            "SUM(parent_external_id IS NULL AND cost_usd IS NULL) AS cost_unknown "
            "FROM conversations GROUP BY project_id) t ON t.project_id = p.id WHERE 1=1"
        )
        params: list = []
        if provider:
            sql += " AND p.provider = ?"
            params.append(provider)
        if text_filter:
            sql += " AND (p.path LIKE ? OR p.name LIKE ?)"
            params += [f"%{text_filter}%"] * 2
        sql += " ORDER BY COALESCE(p.last_activity, '') DESC, p.name"
        return [
            Project(id=r["id"], provider=r["provider"], project_key=r["project_key"], path=r["path"],
                    name=r["name"], last_activity=r["last_activity"],
                    conversation_count=r["conversation_count"], total_tokens=r["total_tokens"],
                    cost_usd=r["cost_usd"], cost_unknown=r["cost_unknown"])
            for r in self._conn.execute(sql, params)
        ]

    def list_project_groups(self, provider: Optional[str] = None, text_filter: str = "") -> List[ProjectGroup]:
        groups: Dict[str, ProjectGroup] = {}
        for p in self.list_projects(provider, text_filter):
            g = groups.get(p.path)
            if g is None:
                groups[p.path] = ProjectGroup(path=p.path, name=p.name, providers=[p.provider],
                                              project_ids=[p.id], conversation_count=p.conversation_count,
                                              last_activity=p.last_activity, total_tokens=p.total_tokens,
                                              cost_usd=p.cost_usd, cost_unknown=p.cost_unknown)
                continue
            g.providers.append(p.provider)
            g.project_ids.append(p.id)
            g.conversation_count += p.conversation_count
            g.total_tokens += p.total_tokens
            g.cost_unknown += p.cost_unknown
            if p.cost_usd is not None:
                g.cost_usd = (g.cost_usd or 0.0) + p.cost_usd
            if (p.last_activity or "") > (g.last_activity or ""):
                g.last_activity = p.last_activity
        return sorted(groups.values(), key=lambda g: (g.last_activity or ""), reverse=True)

    def get_project(self, project_id: int) -> Optional[Project]:
        r = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if r is None:
            return None
        return Project(id=r["id"], provider=r["provider"], project_key=r["project_key"], path=r["path"],
                       name=r["name"], last_activity=r["last_activity"])

    # --------------------------------------------------------- conversations
    def fingerprints(self, provider: str) -> Dict[str, Optional[str]]:
        return {
            r["external_id"]: r["source_fingerprint"]
            for r in self._conn.execute(
                "SELECT external_id, source_fingerprint FROM conversations WHERE provider = ?", (provider,)
            )
        }

    def save_conversation(self, project_id: int, provider: str, source_path: str, fingerprint: str,
                          conv: ParsedConversation) -> int:
        """Insert or replace one conversation and all its message blocks,
        in a single transaction (caller commits)."""
        values = dict(
            project_id=project_id, provider=provider, external_id=conv.external_id,
            parent_external_id=conv.parent_external_id, title=conv.title, first_prompt=conv.first_prompt,
            model=conv.model, cwd=conv.cwd, git_branch=conv.git_branch, started_at=conv.started_at,
            updated_at=conv.updated_at, message_count=conv.message_count,
            user_message_count=conv.user_message_count, tool_call_count=conv.tool_call_count,
            input_tokens=conv.input_tokens, output_tokens=conv.output_tokens,
            cache_read_tokens=conv.cache_read_tokens, cache_write_tokens=conv.cache_write_tokens,
            cost_usd=conv.cost_usd, tools_used=json.dumps(conv.tools_used), source_path=source_path,
            source_fingerprint=fingerprint, missing_from_source=0,
        )
        cols = ", ".join(values)
        placeholders = ", ".join(f":{k}" for k in values)
        updates = ", ".join(f"{k} = excluded.{k}" for k in values if k not in ("provider", "external_id"))
        self._conn.execute(
            f"INSERT INTO conversations ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(provider, external_id) DO UPDATE SET {updates}",
            values,
        )
        conv_id = self._conn.execute(
            "SELECT id FROM conversations WHERE provider = ? AND external_id = ?", (provider, conv.external_id)
        ).fetchone()["id"]
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        self._conn.executemany(
            "INSERT INTO messages (conversation_id, seq, role, kind, tool_name, text, timestamp, is_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(conv_id, b.seq, b.role, b.kind, b.tool_name, b.text, b.timestamp, int(b.is_error))
             for b in conv.blocks],
        )
        return conv_id

    def mark_missing(self, provider: str, present_external_ids: Iterable[str]) -> int:
        """Flag every conversation of `provider` not in `present_external_ids`
        as gone from its source (kept, readable from the archive)."""
        present = set(present_external_ids)
        missing = [ext for ext in self.fingerprints(provider) if ext not in present]
        self._conn.execute(
            "UPDATE conversations SET missing_from_source = 0 WHERE provider = ?", (provider,)
        )
        self._conn.executemany(
            "UPDATE conversations SET missing_from_source = 1 WHERE provider = ? AND external_id = ?",
            [(provider, ext) for ext in missing],
        )
        return len(missing)

    def list_conversations(self, project_ids: Iterable[int]) -> List[Conversation]:
        ids = list(project_ids)
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT * FROM conversations WHERE project_id IN ({marks}) "
            "ORDER BY COALESCE(updated_at, started_at, '') DESC",
            ids,
        ).fetchall()
        return [_conversation_from_row(r) for r in rows]

    def get_conversation(self, conversation_id: int) -> Optional[Conversation]:
        r = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return _conversation_from_row(r) if r else None

    def list_messages(self, conversation_id: int) -> List[MessageBlock]:
        return [
            MessageBlock(role=r["role"], kind=r["kind"], text=r["text"], tool_name=r["tool_name"],
                         timestamp=r["timestamp"], is_error=bool(r["is_error"]), seq=r["seq"], id=r["id"])
            for r in self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
            )
        ]

    def commit(self) -> None:
        self._conn.commit()
