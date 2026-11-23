"""Pure (wx-free) conversation -> HTML rendering for wx.html.HtmlWindow.

wx.html is a small HTML 3.2-ish engine: no CSS, no JS, no wrapping inside
<pre>. So: colours via table bgcolor/font color, collapsing via links
(`toggle:<seq>`) that make ConversationView re-render, and prose rendered as
escaped text with <br> so it wraps (only fenced code goes in <pre>).
"""
import html
import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Set

from app.formatting import format_cost, format_datetime, format_duration, format_tokens
from app.models import Conversation, MessageBlock

MAX_RENDERED_CHARS = 30_000
TOOL_KINDS = ("tool_use", "tool_result")


@dataclass
class Palette:
    bg: str
    fg: str
    muted: str
    user_bg: str
    assistant_bg: str
    tool_bg: str
    thinking_bg: str
    error_bg: str
    link: str
    code_bg: str


LIGHT = Palette(bg="#ffffff", fg="#1f2328", muted="#6e7781", user_bg="#ddf4ff", assistant_bg="#f6f8fa",
                tool_bg="#fff8e6", thinking_bg="#f3eefc", error_bg="#ffebe9", link="#0969da", code_bg="#eaeef2")
DARK = Palette(bg="#1e2228", fg="#e6edf3", muted="#8b949e", user_bg="#12324a", assistant_bg="#262c36",
               tool_bg="#3a3220", thinking_bg="#2e2640", error_bg="#4a1f22", link="#58a6ff", code_bg="#161b22")

ROLE_LABELS = {"user": "You", "assistant": "Assistant", "system": "System"}


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _prose(text: str) -> str:
    """Escaped text that wraps: newlines -> <br>, leading spaces kept."""
    lines = []
    for line in _esc(text).split("\n"):
        stripped = line.lstrip(" ")
        lines.append("&nbsp;" * (len(line) - len(stripped)) + stripped)
    return "<br>".join(lines)


_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def _markdownish(text: str, pal: Palette) -> str:
    """Just enough markdown for readability: fenced code -> <pre>, bold
    headings, the rest as wrapped prose."""
    out: List[str] = []
    pos = 0
    for m in _FENCE.finditer(text):
        out.append(_prose_with_headings(text[pos:m.start()]))
        out.append(_code(m.group(1).rstrip("\n"), pal))
        pos = m.end()
    out.append(_prose_with_headings(text[pos:]))
    return "".join(out)


def _prose_with_headings(text: str) -> str:
    rendered = _prose(text.strip("\n"))
    return re.sub(r"(^|<br>)(#{1,6})&nbsp;?\s*([^<]*)", lambda m: f"{m.group(1)}<b>{m.group(3)}</b>", rendered)


def _code(text: str, pal: Palette) -> str:
    return (f'<table width="100%" cellpadding="6" cellspacing="0"><tr><td bgcolor="{pal.code_bg}">'
            f"<pre>{_esc(text)}</pre></td></tr></table>")


def _clip_for_view(text: str) -> str:
    if len(text) <= MAX_RENDERED_CHARS:
        return text
    return text[:MAX_RENDERED_CHARS] + f"\n\n[... {len(text) - MAX_RENDERED_CHARS:,} more characters not shown ...]"


def tool_summary(block: MessageBlock) -> str:
    """One-line preview of a tool call/result for its collapsed header."""
    text = block.text
    if block.kind == "tool_use":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("command", "file_path", "filePath", "pattern", "url", "query", "description", "prompt"):
                if isinstance(data.get(key), str):
                    text = data[key]
                    break
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return first[:140] + ("..." if len(first) > 140 else "")


def _header(conv: Conversation, project_path: str, provider_label: str, pal: Palette) -> str:
    rows = [
        ("Project", project_path),
        ("Source", provider_label + ("  (archived - no longer on disk)" if conv.missing_from_source else "")),
        ("Session", conv.external_id),
        ("Model", conv.model or ""),
        ("Started", format_datetime(conv.started_at)),
        ("Last activity", format_datetime(conv.updated_at)),
        ("Duration", format_duration(conv.started_at, conv.updated_at)),
        ("Messages", f"{conv.user_message_count} prompts, {conv.message_count} blocks, "
                     f"{conv.tool_call_count} tool calls"),
        ("Tokens", f"in {format_tokens(conv.input_tokens)}, out {format_tokens(conv.output_tokens)}, "
                   f"cache read {format_tokens(conv.cache_read_tokens)}, "
                   f"cache write {format_tokens(conv.cache_write_tokens)}"),
        ("Cost", format_cost(conv.cost_usd)),
        ("Branch", conv.git_branch or ""),
        ("Tools", ", ".join(f"{k} x{v}" for k, v in sorted(conv.tools_used.items(), key=lambda kv: -kv[1]))),
    ]
    body = "".join(
        f'<tr><td valign="top"><font color="{pal.muted}">{_esc(k)}</font></td><td>{_esc(v)}</td></tr>'
        for k, v in rows if v
    )
    return (f'<h3>{_esc(conv.display_title)}</h3>'
            f'<table cellpadding="2" cellspacing="0">{body}</table><hr>')


def render_conversation_html(conv: Conversation, blocks: Iterable[MessageBlock], *, project_path: str,
                             provider_label: str, expanded: Set[int], expand_tools: bool,
                             expand_thinking: bool, pal: Palette = LIGHT) -> str:
    parts = [f'<html><body bgcolor="{pal.bg}" text="{pal.fg}" link="{pal.link}">',
             _header(conv, project_path, provider_label, pal)]
    last_role = None
    for b in blocks:
        collapsible = b.kind in TOOL_KINDS or b.kind == "thinking"
        if collapsible:
            default_open = expand_tools if b.kind in TOOL_KINDS else expand_thinking
            is_open = default_open != (b.seq in expanded)  # click flips the default
        else:
            is_open = True

        if b.role != last_role and not (b.kind == "tool_result" and last_role == "assistant"):
            label = ROLE_LABELS.get(b.role, b.role)
            when = format_datetime(b.timestamp)
            parts.append(f'<p><b>{_esc(label)}</b> <font color="{pal.muted}" size="-1">{_esc(when)}</font></p>')
        last_role = "assistant" if b.kind == "tool_result" else b.role

        if b.kind == "text":
            bg = pal.user_bg if b.role == "user" else pal.assistant_bg
            body = _prose(_clip_for_view(b.text)) if b.role == "user" else _markdownish(_clip_for_view(b.text), pal)
            parts.append(f'<table width="100%" cellpadding="8" cellspacing="0"><tr><td bgcolor="{bg}">'
                         f"{body}</td></tr></table>")
        elif b.kind == "error":
            parts.append(f'<table width="100%" cellpadding="8" cellspacing="0"><tr><td bgcolor="{pal.error_bg}">'
                         f"<b>Error</b><br>{_prose(_clip_for_view(b.text))}</td></tr></table>")
        else:
            arrow = "&#9662;" if is_open else "&#9656;"
            if b.kind == "thinking":
                bg, title = pal.thinking_bg, "Thinking"
                preview = "" if is_open else _esc(tool_summary(b))
            else:
                bg = pal.error_bg if b.is_error else pal.tool_bg
                verb = "Tool" if b.kind == "tool_use" else ("Result (error)" if b.is_error else "Result")
                title = f"{verb}: {b.tool_name or ''}"
                preview = _esc(tool_summary(b))
            head = (f'<a href="toggle:{b.seq}">{arrow} <b>{_esc(title)}</b></a> '
                    f'<font color="{pal.muted}">{preview}</font>')
            content = ""
            if is_open:
                content = ("<br>" + _code(_clip_for_view(b.text), pal)) if b.kind != "thinking" \
                    else "<br>" + _prose(_clip_for_view(b.text))
            parts.append(f'<table width="100%" cellpadding="4" cellspacing="0"><tr><td bgcolor="{bg}">'
                         f"{head}{content}</td></tr></table>")
    if conv.message_count == 0:
        parts.append(f'<p><font color="{pal.muted}">No messages in this conversation.</font></p>')
    parts.append("</body></html>")
    return "".join(parts)
