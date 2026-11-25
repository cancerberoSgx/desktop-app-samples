"""Markdown -> HTML that wx.html.HtmlWindow can display (pure, wx-free).

Uses python-markdown, then rewrites its output for wx.html's HTML-3.2-ish
engine (no CSS): fenced code becomes a bgcolor table, tables get a border,
headings are shrunk to fit inside a message bubble, images become links.

Raw HTML inside the markdown is escaped, not passed through: transcripts are
full of things like `<div>` written as plain prose, and passing them through
would let a reply break the page (or forge a `toggle:` link).

`markdown_available()` is False when the library isn't installed (e.g. an AUR
install missing python-markdown); callers then fall back to the simpler
renderer in conversation_html.
"""
import re
from functools import lru_cache

try:
    import markdown as _markdown_lib
    # Imported as classes (not by name) so PyInstaller sees them.
    from markdown.extensions.fenced_code import FencedCodeExtension
    from markdown.extensions.sane_lists import SaneListExtension
    from markdown.extensions.tables import TableExtension
except ImportError:  # pragma: no cover - exercised only without the dependency
    _markdown_lib = None

_md = None


def markdown_available() -> bool:
    return _markdown_lib is not None


def _converter():
    global _md
    if _md is None:
        _md = _markdown_lib.Markdown(extensions=[FencedCodeExtension(), TableExtension(), SaneListExtension()])
        # Escape raw HTML instead of passing it through.
        _md.preprocessors.deregister("html_block")
        _md.inlinePatterns.deregister("html")
    return _md


_FENCE_LINE = re.compile(r"^( *)(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^( *)([-*+]|\d+[.)])( +|$)")
_TABLE_ROW = re.compile(r"^ *\|")


def _normalize(text: str) -> str:
    """Bend LLM-style markdown (CommonMark-ish) into what python-markdown
    expects: nested list items re-indented to 4 spaces per level (LLMs use
    2-3), a blank line before a list/table that follows a paragraph, and
    fences indented inside list items pulled out to column 0 (python-markdown
    can't fence inside a list)."""
    out = []
    stack = []  # original indents of the open list items
    fence = None  # (marker char, original indent) while inside a fence
    prev_kind = "blank"  # blank | list | table | text
    for line in text.split("\n"):
        if fence is not None:
            m = _FENCE_LINE.match(line)
            if m and m.group(2)[0] == fence[0] and not line.strip().strip(fence[0]):
                out.append(line.strip())
                fence = None
            else:
                indent = len(line) - len(line.lstrip(" "))
                out.append(line[min(indent, fence[1]):])
            continue
        m = _FENCE_LINE.match(line)
        if m:
            fence = (m.group(2)[0], len(m.group(1)))
            if prev_kind != "blank":
                out.append("")
            out.append(line.lstrip(" "))
            prev_kind = "text"
            continue
        if not line.strip():
            out.append(line)
            prev_kind = "blank"
            continue
        m = _LIST_ITEM.match(line)
        indent = len(line) - len(line.lstrip(" "))
        if m:
            while stack and stack[-1] >= indent:
                stack.pop()
            depth = len(stack)
            stack.append(indent)
            if depth == 0 and prev_kind not in ("blank", "list"):
                out.append("")
            out.append(" " * (4 * depth) + line.lstrip(" "))
            prev_kind = "list"
        elif _TABLE_ROW.match(line):
            stack = []
            if prev_kind not in ("blank", "table"):
                out.append("")
            out.append(line)
            prev_kind = "table"
        else:
            if indent and stack:
                line = " " * (4 * len(stack)) + line.lstrip(" ")
            elif not indent:
                stack = []
            out.append(line)
            prev_kind = "list" if stack else "text"
    return "\n".join(out)


_PRE = re.compile(r"<pre><code[^>]*>(.*?)</code></pre>", re.S)
_HEADING = re.compile(r"<(/?)h([1-6])>")
_TABLE = re.compile(r"<table>")
_CELL_ALIGN = re.compile(r'<(td|th) style="text-align: (left|right|center);">')
_IMG = re.compile(r'<img alt="([^"]*)" src="([^"]*)"[^>]*/?>')
_UNSAFE_HREF = re.compile(r'href="(?!https?:|mailto:)[^"]*"')


def _convert(text: str) -> str:
    md = _converter()
    md.reset()
    return md.convert(_normalize(text))


@lru_cache(maxsize=4096)
def markdown_to_wxhtml(text: str, code_bg: str) -> str:
    """Render `text` as markdown; `code_bg` is the code-block background."""
    out = _convert(text)
    out = _PRE.sub(lambda m: (f'<table width="100%" cellpadding="6" cellspacing="0"><tr>'
                              f'<td bgcolor="{code_bg}"><pre>{m.group(1).rstrip()}</pre></td></tr></table>'), out)
    # h1 is huge in wx.html; shift down so the biggest is h3.
    out = _HEADING.sub(lambda m: f"<{m.group(1)}h{min(int(m.group(2)) + 2, 6)}>", out)
    out = _TABLE.sub('<table border="1" cellpadding="4" cellspacing="0">', out)
    out = _CELL_ALIGN.sub(r'<\1 align="\2">', out)
    out = _IMG.sub(lambda m: f'<a href="{m.group(2)}">[image: {m.group(1) or m.group(2)}]</a>', out)
    # Only web links are clickable; anything else (relative paths, toggle:) is inert.
    out = _UNSAFE_HREF.sub("", out)
    return out


@lru_cache(maxsize=4096)
def markdown_to_html(text: str) -> str:
    """Render `text` as markdown for a real browser (the HTML export):
    python-markdown's output kept as is (styled by the page's CSS), with the
    same safety rewrites - raw HTML escaped, only web links clickable, images
    shown as links instead of being fetched."""
    out = _convert(text)
    out = _IMG.sub(lambda m: f'<a href="{m.group(2)}">[image: {m.group(1) or m.group(2)}]</a>', out)
    out = _UNSAFE_HREF.sub("", out)
    return out.replace("<a href=", '<a target="_blank" rel="noopener noreferrer" href=')
