"""Codex CLI: one JSONL "rollout" file per conversation under
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl (CODEX_HOME
defaults to ~/.codex; archived threads move to archived_sessions/).

Every line is {"timestamp", "type", "payload"}. The first is `session_meta`
(id, cwd, git, source); content is in `response_item` lines (message /
reasoning / function_call / custom_tool_call / their *_output /
web_search_call) and `event_msg` lines (user_message = what the user really
typed, token_count, turn_aborted, errors).

The rollout files are the source of truth. Codex's own state database
(state_<N>.sqlite, opened read-only) is optional and only used for user-set
thread names and subagent parent links (`thread_spawn_edges`). The legacy
2025 TypeScript-CLI `rollout-*.json` files carry no working directory and
are skipped.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from app.models import ConversationSource, MessageBlock, ParsedConversation
from app.providers._util import clip, to_text
from app.providers.base import ProviderAdapter

PROVIDER_ID = "codex"
INTERRUPTED = "[Request interrupted by user]"

# User-role messages Codex injects itself (environment, AGENTS.md, ...).
# Only used for old rollouts that lack `user_message` events.
_INJECTED_PREFIXES = ("<environment_context>", "<user_instructions>", "# AGENTS.md instructions",
                      "<turn_aborted>", "<permissions", "<user_shell_command>", "<skills_instructions>")
_EXIT_CODE = re.compile(r"^Exit code: (-?\d+)")
_STATE_DB = re.compile(r"^state_(\d+)\.sqlite$")


def default_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


class CodexProvider(ProviderAdapter):
    id = PROVIDER_ID
    label = "Codex"

    def __init__(self, home: Optional[Path] = None) -> None:
        self.home = Path(home) if home else default_home()

    def is_available(self) -> bool:
        return any(d.is_dir() for d in self._session_dirs())

    def _session_dirs(self) -> List[Path]:
        return [self.home / "sessions", self.home / "archived_sessions"]

    # ------------------------------------------------------------ listing
    def list_sources(self) -> List[ConversationSource]:
        names, parents = self._read_state_db()
        sources: List[ConversationSource] = []
        seen = set()
        for path in self._rollout_files():
            meta = _read_session_meta(path)
            if not meta or not meta.get("id") or not meta.get("cwd"):
                continue
            thread_id = meta["id"]
            if thread_id in seen:  # same thread in sessions/ and archived_sessions/
                continue
            seen.add(thread_id)
            try:
                st = path.stat()
            except OSError:
                continue
            name = names.get(thread_id)
            sources.append(ConversationSource(
                provider=self.id, external_id=thread_id, project_key=meta["cwd"],
                project_path=meta["cwd"], source_path=str(path),
                # the name lives outside the file, so a rename must change the fingerprint too
                fingerprint=f"{st.st_mtime_ns}:{st.st_size}:{name or ''}",
                parent_external_id=parents.get(thread_id) or _spawn_parent(meta.get("source")),
                title_hint=name,
            ))
        return sources

    def _rollout_files(self) -> Iterator[Path]:
        for d in self._session_dirs():
            if d.is_dir():
                yield from sorted(d.rglob("rollout-*.jsonl"))

    def _state_db_path(self) -> Optional[Path]:
        best: Tuple[int, Optional[Path]] = (-1, None)
        try:
            for p in self.home.iterdir():
                m = _STATE_DB.match(p.name)
                if m and int(m.group(1)) > best[0]:
                    best = (int(m.group(1)), p)
        except OSError:
            pass
        return best[1]

    def _read_state_db(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """(thread id -> user-set name, child thread id -> parent id); empty
        if the database is missing or its schema is not what we expect."""
        names: Dict[str, str] = {}
        parents: Dict[str, str] = {}
        path = self._state_db_path()
        if path is None:
            return names, parents
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            return names, parents
        try:
            try:
                for tid, name in conn.execute("SELECT id, name FROM threads WHERE name IS NOT NULL AND name != ''"):
                    names[tid] = name
            except sqlite3.Error:
                pass
            try:
                for parent, child in conn.execute("SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"):
                    parents[child] = parent
            except sqlite3.Error:
                pass
        finally:
            conn.close()
        return names, parents

    # ------------------------------------------------------------ parsing
    def parse(self, source: ConversationSource) -> ParsedConversation:
        conv = ParsedConversation(external_id=source.external_id, project_path=source.project_path,
                                  parent_external_id=source.parent_external_id, title=source.title_hint)
        lines = list(_read_lines(Path(source.source_path)))
        has_prompt_events = any(_is_prompt_event(p) for t, p, _ts in lines if t == "event_msg")
        tool_names: Dict[str, str] = {}
        usage: Optional[dict] = None

        def add(role: str, kind: str, text: str, ts: Optional[str], **kw) -> None:
            conv.blocks.append(MessageBlock(role, kind, clip(text), timestamp=ts, **kw))

        def add_prompt(text: str, ts: Optional[str]) -> None:
            if not text.strip():
                return
            add("user", "text", text, ts)
            if conv.first_prompt is None:
                conv.first_prompt = text.strip()[:500]

        for kind, p, ts in lines:
            if ts and conv.started_at is None:
                conv.started_at = ts
            if ts:
                conv.updated_at = ts
            t = p.get("type")
            if kind == "session_meta":
                conv.cwd = p.get("cwd")
                if p.get("timestamp"):
                    conv.started_at = p["timestamp"]
                git = p.get("git")
                if isinstance(git, dict):
                    conv.git_branch = git.get("branch")
            elif kind == "turn_context":
                if p.get("model"):
                    conv.model = p["model"]
            elif kind == "response_item":
                if t == "message":
                    role = p.get("role")
                    text = _content_text(p.get("content"))
                    if role == "assistant":
                        if text.strip():
                            add("assistant", "text", text, ts)
                    elif role == "user" and not has_prompt_events and not text.lstrip().startswith(_INJECTED_PREFIXES):
                        add_prompt(text, ts)
                elif t == "reasoning":
                    text = "\n\n".join(s.get("text", "") for s in p.get("summary") or []
                                       if isinstance(s, dict) and s.get("text"))
                    if not text and isinstance(p.get("content"), list):
                        text = _content_text(p["content"])
                    if text.strip():
                        add("assistant", "thinking", text, ts)
                elif t in ("function_call", "custom_tool_call", "local_shell_call"):
                    name = p.get("name") or ("shell" if t == "local_shell_call" else "tool")
                    tool_names[p.get("call_id") or ""] = name
                    conv.tools_used[name] = conv.tools_used.get(name, 0) + 1
                    raw = p.get("arguments") if t == "function_call" else p.get("input", p.get("action"))
                    add("assistant", "tool_use", _tool_input(raw), ts, tool_name=name)
                elif t in ("function_call_output", "custom_tool_call_output"):
                    name = tool_names.get(p.get("call_id") or "", "tool")
                    text, is_error = _tool_output(p.get("output"))
                    add("assistant", "tool_result", text, ts, tool_name=name, is_error=is_error)
                elif t == "web_search_call":
                    action = p.get("action") or {}
                    conv.tools_used["web_search"] = conv.tools_used.get("web_search", 0) + 1
                    add("assistant", "tool_use", action.get("query") or to_text(action), ts, tool_name="web_search")
                elif t == "compaction" or t == "compacted":
                    add("system", "text", "[context compacted]", ts)
            elif kind == "compacted":
                add("system", "text", "[context compacted]", ts)
            elif kind == "event_msg":
                if t == "user_message":
                    add_prompt(p.get("message") or "", ts)
                elif t == "item_completed" and _is_user_item(p):
                    add_prompt(_content_text(p["item"].get("content")), ts)
                elif t == "token_count":
                    info = p.get("info")
                    if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                        usage = info["total_token_usage"]
                elif t == "turn_aborted":
                    reason = p.get("reason") or "interrupted"
                    add("user", "text", INTERRUPTED if reason == "interrupted" else f"[Request {reason}]", ts)
                elif t in ("error", "stream_error"):
                    add("assistant", "error", p.get("message") or to_text(p), ts, is_error=True)
                elif t == "task_complete" and isinstance(p.get("error"), dict):
                    add("assistant", "error", p["error"].get("message") or to_text(p["error"]), ts, is_error=True)

        if usage:
            cached = usage.get("cached_input_tokens") or 0
            conv.input_tokens = max((usage.get("input_tokens") or 0) - cached, 0)
            conv.cache_read_tokens = cached
            conv.output_tokens = usage.get("output_tokens") or 0  # includes reasoning tokens
        for seq, block in enumerate(conv.blocks):
            block.seq = seq
        return conv


def _read_lines(path: Path) -> Iterator[Tuple[str, dict, Optional[str]]]:
    try:
        f = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("payload"), dict):
                yield d.get("type") or "", d["payload"], d.get("timestamp")


def _read_session_meta(path: Path) -> Optional[dict]:
    for kind, payload, _ts in _read_lines(path):
        return payload if kind == "session_meta" else None
    return None


def _spawn_parent(source) -> Optional[str]:
    """Subagent rollouts record their parent in session_meta.source, e.g.
    {"subagent": {"thread_spawn": {"parent_thread_id": ...}}}."""
    if not isinstance(source, dict):
        return None
    sub = source.get("subagent")
    if isinstance(sub, dict):
        spawn = sub.get("thread_spawn")
        if isinstance(spawn, dict) and spawn.get("parent_thread_id"):
            return spawn["parent_thread_id"]
    return None


def _is_prompt_event(p: dict) -> bool:
    return p.get("type") == "user_message" or (p.get("type") == "item_completed" and _is_user_item(p))


def _is_user_item(p: dict) -> bool:
    item = p.get("item")
    return isinstance(item, dict) and item.get("type") == "UserMessage"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get("text"):
            parts.append(c["text"])
        elif c.get("type") in ("input_image", "image", "local_image"):
            parts.append("[image]")
    return "\n\n".join(parts)


def _tool_input(raw) -> str:
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return raw  # apply_patch input is the raw patch
    else:
        value = raw
    if isinstance(value, dict):
        cmd = value.get("command", value.get("cmd"))
        if isinstance(cmd, list) and cmd[:2] in (["bash", "-lc"], ["sh", "-c"]) and len(cmd) == 3:
            cmd = cmd[2]
        if isinstance(cmd, str) and set(value) <= {"command", "cmd", "workdir", "timeout_ms", "yield_time_ms",
                                                   "max_output_tokens", "login", "shell"}:
            return cmd
    return to_text(value)


def _tool_output(raw) -> Tuple[str, bool]:
    """Tool outputs are either plain text ("Exit code: N\\nWall time: ...\\n
    Output:\\n...") or JSON {"output": ..., "metadata": {"exit_code": N}}."""
    if isinstance(raw, dict):
        raw = raw.get("content", raw)
    if not isinstance(raw, str):
        return to_text(raw), False
    if raw.startswith("{"):
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if isinstance(value, dict) and "output" in value:
            code = (value.get("metadata") or {}).get("exit_code")
            return to_text(value["output"]), bool(code)
    m = _EXIT_CODE.match(raw)
    return raw, bool(m and int(m.group(1)) != 0)
