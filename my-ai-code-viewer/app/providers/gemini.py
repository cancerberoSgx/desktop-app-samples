"""Gemini CLI: ~/.gemini/tmp/<project>/chats/session-<time>-<id>.jsonl, one
file per chat. <project> is a short name (older CLIs: the sha256 of the
project path); the real path is in <project>/.project_root, or in
~/.gemini/projects.json ({"projects": {path: name}}).

JSONL format (CLI >= ~0.60): the first line is a header {sessionId,
projectHash, startTime, lastUpdated, kind}; then records {id, timestamp,
type: user|gemini|error|info|..., content, thoughts, tokens, model,
toolCalls}. A record is re-appended under the same id when it grows (e.g.
the gemini turn gets its toolCalls), so the last version of an id wins but
keeps its first position. {"$set": {...}} lines update header fields.
Older CLIs wrote one .json document {sessionId, ..., messages: [...]} with
the same record shape - both are read.

Tool results are inline in toolCalls[].result, so the user-role records
that echo them back as functionResponse parts are skipped, as is the
injected <session_context> first message.
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from app.models import ConversationSource, MessageBlock, ParsedConversation
from app.providers._util import clip, to_text
from app.providers.base import ProviderAdapter

PROVIDER_ID = "gemini"
_INJECTED_PREFIXES = ("<session_context>",)


def default_home() -> Path:
    return Path.home() / ".gemini"


class GeminiProvider(ProviderAdapter):
    id = PROVIDER_ID
    label = "Gemini CLI"

    def __init__(self, home: Optional[Path] = None) -> None:
        self.home = Path(home) if home else default_home()

    def is_available(self) -> bool:
        return (self.home / "tmp").is_dir()

    def list_sources(self) -> List[ConversationSource]:
        if not self.is_available():
            return []
        known = self._known_projects()
        sources: List[ConversationSource] = []
        for project_dir in sorted(p for p in (self.home / "tmp").iterdir() if p.is_dir()):
            chats = project_dir / "chats"
            if not chats.is_dir():
                continue
            root = _read_project_root(project_dir) or known.get(project_dir.name)
            for path in sorted(chats.glob("session-*.json*")):
                if path.suffix not in (".json", ".jsonl"):
                    continue
                header = _read_header(path)
                if header is None or not header.get("sessionId"):
                    continue
                project = root or known.get(header.get("projectHash") or "")
                if not project:
                    continue  # no way to tell which folder it belongs to
                try:
                    st = path.stat()
                except OSError:
                    continue
                sources.append(ConversationSource(
                    provider=self.id, external_id=header["sessionId"], project_key=project,
                    project_path=project, source_path=str(path),
                    fingerprint=f"{st.st_mtime_ns}:{st.st_size}",
                    parent_external_id=header.get("parentSessionId"),
                ))
        return sources

    def _known_projects(self) -> Dict[str, str]:
        """tmp-dir name or sha256(path) -> project path, from projects.json
        and history/<name>/.project_root."""
        known: Dict[str, str] = {}
        paths: List[str] = []
        try:
            data = json.loads((self.home / "projects.json").read_text(encoding="utf-8"))
            for path, name in (data.get("projects") or {}).items():
                known[name] = path
                paths.append(path)
        except (OSError, ValueError, AttributeError):
            pass
        history = self.home / "history"
        if history.is_dir():
            for d in history.iterdir():
                root = _read_project_root(d)
                if root:
                    known.setdefault(d.name, root)
                    paths.append(root)
        for path in paths:
            known.setdefault(hashlib.sha256(path.encode("utf-8")).hexdigest(), path)
        return known

    def parse(self, source: ConversationSource) -> ParsedConversation:
        conv = ParsedConversation(external_id=source.external_id, project_path=source.project_path,
                                  parent_external_id=source.parent_external_id, cwd=source.project_path)
        header, records = _load(Path(source.source_path))
        conv.started_at = header.get("startTime")
        conv.updated_at = header.get("lastUpdated")
        summary = header.get("summary")
        if isinstance(summary, str) and summary.strip():
            conv.title = summary.strip()

        def add(role: str, kind: str, text: str, ts: Optional[str], **kw) -> None:
            conv.blocks.append(MessageBlock(role, kind, clip(text), timestamp=ts, **kw))

        for r in records:
            ts = r.get("timestamp")
            t = r.get("type")
            if ts and (conv.updated_at is None or ts > conv.updated_at):
                conv.updated_at = ts
            if t == "user":
                text = _content_text(r.get("displayContent") or r.get("content"))
                if not text.strip() or text.lstrip().startswith(_INJECTED_PREFIXES):
                    continue
                add("user", "text", text, ts)
                if conv.first_prompt is None:
                    conv.first_prompt = text.strip()[:500]
            elif t == "gemini":
                if r.get("model"):
                    conv.model = r["model"]
                _add_tokens(conv, r.get("tokens"))
                thoughts = _thoughts_text(r.get("thoughts"))
                if thoughts:
                    add("assistant", "thinking", thoughts, ts)
                text = _content_text(r.get("content"))
                if text.strip():
                    add("assistant", "text", text, ts)
                for call in r.get("toolCalls") or []:
                    if not isinstance(call, dict):
                        continue
                    name = call.get("name") or "tool"
                    cts = call.get("timestamp") or ts
                    conv.tools_used[name] = conv.tools_used.get(name, 0) + 1
                    add("assistant", "tool_use", to_text(call.get("args")), cts, tool_name=name)
                    result = _tool_result(call)
                    if result is not None:
                        add("assistant", "tool_result", result, cts, tool_name=name,
                            is_error=call.get("status") not in (None, "success"))
            elif t == "error":
                add("assistant", "error", _content_text(r.get("content")), ts, is_error=True)
            elif t == "warning":
                add("system", "text", _content_text(r.get("content")), ts)
            # "info" records are CLI notices (update available, ...) - not part of the chat

        for seq, block in enumerate(conv.blocks):
            block.seq = seq
        return conv


# ---------------------------------------------------------------- reading
def _read_project_root(d: Path) -> Optional[str]:
    try:
        root = (d / ".project_root").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return root or None


def _json_lines(path: Path) -> Iterator[dict]:
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
            if isinstance(d, dict):
                yield d


def _read_header(path: Path) -> Optional[dict]:
    if path.suffix == ".json":
        doc = _read_json_doc(path)
        return doc if doc and "messages" in doc else None
    for d in _json_lines(path):
        return d if "sessionId" in d else None
    return None


def _read_json_doc(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _load(path: Path) -> Tuple[dict, List[dict]]:
    """(header, records in order, latest version of each id)."""
    if path.suffix == ".json":
        doc = _read_json_doc(path) or {}
        messages = [m for m in doc.get("messages") or [] if isinstance(m, dict)]
        return {k: v for k, v in doc.items() if k != "messages"}, messages
    header: dict = {}
    by_id: Dict[str, dict] = {}
    order: List[str] = []
    for i, d in enumerate(_json_lines(path)):
        if i == 0 and "sessionId" in d:
            header = dict(d)
        elif "$set" in d:
            if isinstance(d["$set"], dict):
                header.update(d["$set"])
        elif "$rewindTo" in d:
            # conversation rewound to just before this message id
            target = d["$rewindTo"]
            if target in by_id:
                keep = order[:order.index(target)]
                by_id = {k: by_id[k] for k in keep}
                order = keep
        elif d.get("type"):
            key = d.get("id") or f"#{i}"
            if key not in by_id:
                order.append(key)
            by_id[key] = d
    return header, [by_id[k] for k in order]


def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    parts = []
    for c in content:
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, dict) and isinstance(c.get("text"), str):
            parts.append(c["text"])
        elif isinstance(c, dict) and "inlineData" in c:
            parts.append(f"[{(c['inlineData'] or {}).get('mimeType') or 'attachment'}]")
    return "\n\n".join(p for p in parts if p)


def _thoughts_text(thoughts) -> str:
    out = []
    for t in thoughts or []:
        if not isinstance(t, dict):
            continue
        subject, desc = t.get("subject"), t.get("description")
        out.append("\n\n".join(x for x in (f"**{subject}**" if subject else None, desc) if x))
    return "\n\n".join(x for x in out if x)


def _tool_result(call: dict) -> Optional[str]:
    texts = []
    for part in call.get("result") or []:
        if not isinstance(part, dict):
            continue
        resp = (part.get("functionResponse") or {}).get("response")
        if isinstance(resp, dict):
            value = resp.get("output", resp.get("error", resp))
            texts.append(value if isinstance(value, str) else to_text(value))
        elif resp is not None:
            texts.append(to_text(resp))
    if texts:
        return "\n".join(texts)
    display = call.get("resultDisplay")
    if isinstance(display, str) and display:
        return display
    if display:
        return to_text(display)
    status = call.get("status")
    return f"[{status}]" if status and status != "success" else None


def _add_tokens(conv: ParsedConversation, tokens) -> None:
    if not isinstance(tokens, dict):
        return
    cached = tokens.get("cached") or 0
    conv.input_tokens += max((tokens.get("input") or 0) - cached, 0)
    conv.cache_read_tokens += cached
    conv.output_tokens += (tokens.get("output") or 0) + (tokens.get("thoughts") or 0)
