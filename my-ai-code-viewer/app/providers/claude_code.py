"""Claude Code: ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl

Folder names encode the launch directory by replacing every non-alphanumeric
character with '-' (so '/home/sg/my-app' -> '-home-sg-my-app'), which is
lossy - the real path is recovered from the `cwd` field inside the
transcripts instead (see _pick_project_path).

Each JSONL line is one event. Only `user`/`assistant` lines are conversation
content; an assistant API message is split over several lines (one per
content block) sharing the same message.id. Everything else (`attachment`,
`api-request*`, `file-history-*`, `mode`, ...) is bookkeeping, except a few
lines that carry metadata: `ai-title`, `summary`, `cost-state`.

Subagent threads live next to their parent at
<encoded-cwd>/<session-uuid>/subagents/agent-<id>.jsonl (+ agent-<id>.meta.json).
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from app.models import ConversationSource, MessageBlock, ParsedConversation
from app.providers._util import clip, to_text
from app.providers.base import ProviderAdapter

PROVIDER_ID = "claude-code"


def default_root() -> Path:
    return Path.home() / ".claude" / "projects"


def encode_path(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def naive_decode(folder_name: str) -> str:
    """Best-effort fallback when no transcript reveals the real cwd: assume
    every '-' was a '/'. Wrong for paths containing '-', '.', '_' etc."""
    return folder_name.replace("-", "/") if folder_name.startswith("-") else folder_name


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


class ClaudeCodeProvider(ProviderAdapter):
    id = PROVIDER_ID
    label = "Claude Code"

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else default_root()

    def is_available(self) -> bool:
        return self.root.is_dir()

    def list_sources(self) -> List[ConversationSource]:
        sources: List[ConversationSource] = []
        if not self.is_available():
            return sources
        for project_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            key = project_dir.name
            fallback_path = naive_decode(key)
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                sources.append(ConversationSource(
                    provider=self.id, external_id=jsonl.stem, project_key=key,
                    project_path=fallback_path, source_path=str(jsonl),
                    fingerprint=_fingerprint(jsonl),
                ))
            for agent_file in sorted(project_dir.glob("*/subagents/agent-*.jsonl")):
                parent_id = agent_file.parent.parent.name
                agent_id = agent_file.stem
                sources.append(ConversationSource(
                    provider=self.id, external_id=f"{parent_id}:{agent_id}", project_key=key,
                    project_path=fallback_path, source_path=str(agent_file),
                    fingerprint=_fingerprint(agent_file), parent_external_id=parent_id,
                    title_hint=_read_agent_description(agent_file),
                ))
        return sources

    def parse(self, source: ConversationSource) -> ParsedConversation:
        conv = parse_transcript(Path(source.source_path), source.external_id)
        conv.parent_external_id = source.parent_external_id
        if source.parent_external_id and source.title_hint:
            conv.title = source.title_hint
        conv.project_path = _pick_project_path(source.project_key, conv) or source.project_path
        return conv


def _read_agent_description(agent_file: Path) -> Optional[str]:
    meta = agent_file.with_name(agent_file.stem + ".meta.json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    parts = [data.get("agentType"), data.get("description")]
    text = ": ".join(p for p in parts if p)
    return text or None


def _pick_project_path(project_key: str, conv: ParsedConversation) -> Optional[str]:
    candidates = getattr(conv, "_cwds", [])
    for cwd in candidates:
        if encode_path(cwd) == project_key:
            return cwd
    return None


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(to_text(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return to_text(content)


def parse_transcript(path: Path, external_id: str) -> ParsedConversation:
    conv = ParsedConversation(external_id=external_id)
    cwds: List[str] = []
    seen_uuids = set()
    usage_by_message: Dict[str, dict] = {}
    tool_names: Dict[str, str] = {}
    summary: Optional[str] = None
    ai_title: Optional[str] = None

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")

            if kind == "ai-title" and obj.get("aiTitle"):
                ai_title = obj["aiTitle"]
                continue
            if kind == "summary" and obj.get("summary"):
                summary = obj["summary"]
                continue
            if kind == "cost-state" and obj.get("totalCostUSD") is not None:
                conv.cost_usd = float(obj["totalCostUSD"])
                continue
            if kind not in ("user", "assistant"):
                continue

            uuid = obj.get("uuid")
            if uuid:
                if uuid in seen_uuids:
                    continue
                seen_uuids.add(uuid)

            cwd = obj.get("cwd")
            if cwd and cwd not in cwds:
                cwds.append(cwd)
            if conv.cwd is None and cwd:
                conv.cwd = cwd
            if conv.git_branch is None and obj.get("gitBranch"):
                conv.git_branch = obj["gitBranch"]

            if obj.get("isMeta"):
                continue

            ts = obj.get("timestamp")
            if ts:
                if conv.started_at is None or ts < conv.started_at:
                    conv.started_at = ts
                if conv.updated_at is None or ts > conv.updated_at:
                    conv.updated_at = ts

            message = obj.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content")

            if kind == "user":
                _parse_user(conv, content, ts, tool_names)
            else:
                _parse_assistant(conv, message, content, ts, tool_names, usage_by_message)

    for usage in usage_by_message.values():
        conv.input_tokens += int(usage.get("input_tokens") or 0)
        conv.output_tokens += int(usage.get("output_tokens") or 0)
        conv.cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
        conv.cache_write_tokens += int(usage.get("cache_creation_input_tokens") or 0)

    conv.title = ai_title or summary
    if conv.first_prompt is None:
        # Every prompt started with markup (slash commands, pasted blocks):
        # fall back to the first user text with tags stripped.
        first = next((b.text for b in conv.blocks if b.role == "user" and b.kind == "text"), None)
        if first:
            stripped = re.sub(r"<[^>]+>", " ", first).strip()
            conv.first_prompt = (stripped or first.strip())[:500]
    for seq, block in enumerate(conv.blocks):
        block.seq = seq
    conv._cwds = cwds  # type: ignore[attr-defined] - consumed by _pick_project_path
    return conv


def _parse_user(conv: ParsedConversation, content, ts, tool_names: Dict[str, str]) -> None:
    if isinstance(content, str):
        if content.strip():
            conv.blocks.append(MessageBlock("user", "text", clip(content), timestamp=ts))
            if conv.first_prompt is None and not content.lstrip().startswith("<"):
                conv.first_prompt = content.strip()[:500]
        return
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "text":
            text = item.get("text", "")
            if text.strip():
                conv.blocks.append(MessageBlock("user", "text", clip(text), timestamp=ts))
                if conv.first_prompt is None and not text.lstrip().startswith("<"):
                    conv.first_prompt = text.strip()[:500]
        elif t == "tool_result":
            conv.blocks.append(MessageBlock(
                "user", "tool_result", clip(_tool_result_text(item.get("content"))),
                tool_name=tool_names.get(item.get("tool_use_id", "")), timestamp=ts,
                is_error=bool(item.get("is_error")),
            ))
        elif t == "image":
            conv.blocks.append(MessageBlock("user", "text", "[image]", timestamp=ts))


def _parse_assistant(conv, message, content, ts, tool_names, usage_by_message) -> None:
    model = message.get("model")
    if model and model != "<synthetic>":
        conv.model = model
    msg_id = message.get("id")
    if msg_id and isinstance(message.get("usage"), dict):
        usage_by_message[msg_id] = message["usage"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "text" and item.get("text", "").strip():
            conv.blocks.append(MessageBlock("assistant", "text", clip(item["text"]), timestamp=ts))
        elif t == "thinking" and item.get("thinking", "").strip():
            conv.blocks.append(MessageBlock("assistant", "thinking", clip(item["thinking"]), timestamp=ts))
        elif t in ("tool_use", "server_tool_use"):
            name = item.get("name") or "tool"
            if item.get("id"):
                tool_names[item["id"]] = name
            conv.tools_used[name] = conv.tools_used.get(name, 0) + 1
            conv.blocks.append(MessageBlock(
                "assistant", "tool_use", clip(to_text(item.get("input"))), tool_name=name, timestamp=ts,
            ))
