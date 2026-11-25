"""Conversation -> one self-contained, interactive HTML file (pure, wx-free).

Backs File > Export conversation. Unlike conversation_html (which targets
wx.html: no CSS, no JS, re-render on every click), this targets a real
browser and is rendered once: every block is in the page and the viewer's
options are flipped client-side by inline CSS/JS:

- "Q&A only": blocks that aren't a prompt or a final answer carry `.nonqa`,
  hidden under `body.qa-only`. Role headers depend on which blocks are
  visible, so each header is computed for both modes (`.h-full` / `.h-qa`).
- "Render markdown": assistant text and thinking are embedded twice - `.md`
  (python-markdown) and `.alt-plain` (fences + prose) - and `body.md-off`
  picks one. Without the library only the plain version exists.
- "Expand tool calls / thinking": collapsibles are `<details>`, so a click
  flips one block and the checkbox opens/closes them all (like the app).
- Outline (sidebar with scroll-spy), search, light/dark theme and copy
  buttons on code blocks are page-only features.

No external resources at all (works offline, forever). All transcript text
is escaped; markdown goes through markdown_html's sanitising converter.
"""
import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from app.conversation_html import ROLE_LABELS, TOOL_KINDS, header_rows, tool_summary
from app.conversation_turns import first_line, split_turns
from app.formatting import format_datetime
from app.markdown_html import markdown_available, markdown_to_html
from app.models import Conversation, MessageBlock


@dataclass
class ExportOptions:
    """Initial state of the page's toggles (the viewer can flip them)."""
    show_outline: bool = False
    qa_only: bool = True
    expand_tools: bool = False
    expand_thinking: bool = False
    render_markdown: bool = True
    # The app's theme ("system" | "light" | "dark") - the page's default until
    # the reader picks one with its theme button ("system" = auto).
    theme: str = "system"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def _prose(text: str) -> str:
    text = text.strip("\n")
    return f'<div class="prose">{_esc(text)}</div>' if text.strip() else ""


def _plain_rich(text: str) -> str:
    """Markdown off: fenced code as <pre>, headings bold, the rest verbatim."""
    out: List[str] = []
    pos = 0

    def prose(chunk: str) -> str:
        chunk = chunk.strip("\n")
        if not chunk.strip():
            return ""
        escaped = _HEADING_LINE.sub(lambda m: f"<b>{m.group(2)}</b>", _esc(chunk))
        return f'<div class="prose">{escaped}</div>'

    for m in _FENCE.finditer(text):
        out.append(prose(text[pos:m.start()]))
        out.append(f"<pre><code>{_esc(m.group(1).rstrip())}</code></pre>")
        pos = m.end()
    out.append(prose(text[pos:]))
    return "".join(out)


def _rich(text: str, md_ok: bool) -> str:
    if not md_ok:
        return f'<div class="alt-plain">{_plain_rich(text)}</div>'
    return f'<div class="md">{markdown_to_html(text)}</div><div class="alt-plain">{_plain_rich(text)}</div>'


def _header_seqs(blocks: Sequence[MessageBlock], prompt_start: Set[int]) -> Set[int]:
    """Seqs of the blocks that get a role header - same rule as the app:
    on every prompt and whenever the speaker changes (a tool result counts
    as the assistant's)."""
    shown: Set[int] = set()
    last_role = None
    for b in blocks:
        if b.seq in prompt_start or (b.role != last_role and not (b.kind == "tool_result" and last_role == "assistant")):
            shown.add(b.seq)
        last_role = "assistant" if b.kind == "tool_result" else b.role
    return shown


def _render_blocks(blocks: List[MessageBlock], prefix: str, options: ExportOptions,
                   expanded: Set[int], md_ok: bool) -> Tuple[str, list]:
    """Blocks as HTML; `prefix` keeps element ids unique per conversation."""
    turns = split_turns(blocks)
    prompt_start = {t.first_seq: t.number for t in turns}
    answer_of = {t.answer_seq: t.number for t in turns if t.answer_seq is not None}
    keep = {seq for t in turns for seq in t.prompt_seqs} | set(answer_of)
    hdr_full = _header_seqs(blocks, set(prompt_start))
    hdr_qa = _header_seqs([b for b in blocks if b.seq in keep], set(prompt_start))

    parts: List[str] = []
    for b in blocks:
        classes = ["blk", f"k-{b.kind}", f"r-{b.role}"]
        if b.seq not in keep:
            classes.append("nonqa")
        turn_no = prompt_start.get(b.seq)
        answer_no = answer_of.get(b.seq)
        ident = ""
        if turn_no is not None:
            ident = f' id="{prefix}turn-{turn_no}"'
        elif answer_no is not None:
            ident = f' id="{prefix}answer-{answer_no}"'
        parts.append(f'<div class="{" ".join(classes)}"{ident}>')

        in_full, in_qa = b.seq in hdr_full, b.seq in hdr_qa
        if in_full or in_qa:
            hcls = "hdr" + (" h-full" if in_full else "") + (" h-qa" if in_qa else "")
            number = (f'<a class="turn-no" href="#{prefix}turn-{turn_no}">#{turn_no}</a>'
                      if turn_no is not None else "")
            label = ROLE_LABELS.get(b.role, b.role)
            parts.append(f'<div class="{hcls}">{number}<span class="role">{_esc(label)}</span>'
                         f'<span class="when">{_esc(format_datetime(b.timestamp))}</span></div>')

        if b.kind == "text":
            if b.role == "user":
                parts.append(f'<div class="bubble user">{_prose(b.text)}</div>')
            else:
                badge = (f'<a class="answer-badge" href="#{prefix}turn-{answer_no}">Answer to #{answer_no}</a>'
                         if answer_no is not None else "")
                parts.append(f'<div class="bubble assistant">{badge}{_rich(b.text, md_ok)}</div>')
        elif b.kind == "error":
            parts.append(f'<div class="bubble error"><b>Error</b>{_prose(b.text)}</div>')
        else:
            if b.kind == "thinking":
                default_open = options.expand_thinking
                fold_cls, title, body = "thinking", "Thinking", _rich(b.text, md_ok)
                preview = f'<span class="preview hide-open">{_esc(tool_summary(b))}</span>'
            else:
                default_open = options.expand_tools
                fold_cls = "tool err" if b.is_error else "tool"
                verb = "Tool" if b.kind == "tool_use" else ("Result (error)" if b.is_error else "Result")
                title = f"{verb}: {b.tool_name or ''}"
                preview = f'<span class="preview">{_esc(tool_summary(b))}</span>'
                body = f"<pre><code>{_esc(b.text)}</code></pre>"
            is_open = default_open != (b.seq in expanded)
            parts.append(f'<details class="fold {fold_cls}"{" open" if is_open else ""}><summary>'
                         f'<b>{_esc(title)}</b>{preview}</summary><div class="fold-body">{body}</div></details>')
        parts.append("</div>")
    if not blocks:
        parts.append('<p class="muted">No messages in this conversation.</p>')
    return "".join(parts), turns


def _meta_table(conv: Conversation, project_path: str, provider_label: str) -> str:
    rows = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
                   for k, v in header_rows(conv, project_path, provider_label))
    return f'<table class="meta">{rows}</table>'


def _outline(turns) -> str:
    items = []
    for t in turns:
        prompt = _esc(first_line(t.label_text)) or "<i>(empty prompt)</i>"
        if t.answer_seq is not None:
            answer = (f'<a class="o-answer" href="#answer-{t.number}">&#8627; '
                      f'{_esc(first_line(t.answer_text, 120)) or "answer"}</a>')
        else:
            answer = '<span class="o-answer none">&#8627; no answer</span>'
        items.append(f'<li data-turn="{t.number}"><a class="o-prompt" href="#turn-{t.number}">'
                     f'<span class="o-no">#{t.number}</span> {prompt}</a>{answer}</li>')
    if not items:
        items.append('<li class="muted">No prompts.</li>')
    return f'<nav id="outline" aria-label="Outline"><ol>{"".join(items)}</ol></nav>'


_TOGGLES = (
    ("outline", "Outline", "show_outline", "List of prompts and answers; click one to jump to it"),
    ("qa", "Q&A only", "qa_only", "Show only each prompt and its final answer"),
    ("tools", "Expand tool calls", "expand_tools", "Expand or collapse every tool call and result"),
    ("thinking", "Expand thinking", "expand_thinking", "Expand or collapse every thinking block"),
    ("markdown", "Render markdown", "render_markdown", "Render assistant replies and thinking as markdown"),
)


def _page_theme(app_theme: str) -> str:
    return app_theme if app_theme in ("light", "dark") else "auto"


def render_export_html(conv: Conversation, blocks: Iterable[MessageBlock], *, project_path: str,
                       provider_label: str, options: ExportOptions, expanded: Optional[Set[int]] = None,
                       subagents: Sequence[Tuple[Conversation, List[MessageBlock]]] = (),
                       app_version: str = "", exported_at: Optional[datetime] = None) -> str:
    """The whole page. `expanded` = seqs the user flipped in the app (so the
    export opens exactly as it looked); `subagents` are appended as
    collapsible sections."""
    md_ok = markdown_available()
    body_html, turns = _render_blocks(list(blocks), "", options, set(expanded or ()), md_ok)

    body_cls = []
    if options.show_outline:
        body_cls.append("show-outline")
    if options.qa_only:
        body_cls.append("qa-only")
    if not (options.render_markdown and md_ok):
        body_cls.append("md-off")

    toggles = []
    for key, label, attr, tip in _TOGGLES:
        checked = " checked" if getattr(options, attr) and (key != "markdown" or md_ok) else ""
        disabled = " disabled" if key == "markdown" and not md_ok else ""
        toggles.append(f'<label title="{_esc(tip)}"><input type="checkbox" data-opt="{key}"{checked}{disabled}>'
                       f" {_esc(label)}</label>")

    subs = []
    for i, (sub, sub_blocks) in enumerate(subagents, start=1):
        sub_html, _ = _render_blocks(list(sub_blocks), f"s{i}-", options, set(), md_ok)
        stats = f"{sub.user_message_count} prompts &middot; {sub.tool_call_count} tool calls"
        subs.append(f'<details class="subagent" id="sub-{i}"><summary><b>{_esc(sub.display_title)}</b>'
                    f'<span class="preview">{stats}</span></summary><div class="sub-body">'
                    f"{_meta_table(sub, project_path, provider_label)}{sub_html}</div></details>")
    subs_html = (f'<section id="subagents"><h2>Subagents ({len(subs)})</h2>{"".join(subs)}</section>'
                 if subs else "")

    when = (exported_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    version = f" {_esc(app_version)}" if app_version else ""
    title = _esc(conv.display_title)
    archived = '<span class="tag">archived</span>' if conv.missing_from_source else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="My AI Code Viewer{version}">
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body class="{" ".join(body_cls)}" data-default-theme="{_esc(_page_theme(options.theme))}">
<header id="bar">
  <div class="bar-title" title="{title}">{title}</div>
  <div class="bar-opts">{"".join(toggles)}</div>
  <div class="bar-tools">
    <div class="search"><input type="search" id="q" placeholder="Search  ( / )" aria-label="Search">
      <span id="qcount"></span>
      <button type="button" id="qprev" title="Previous match (Shift+Enter)" aria-label="Previous match">&#8593;</button>
      <button type="button" id="qnext" title="Next match (Enter)" aria-label="Next match">&#8595;</button></div>
    <button type="button" id="theme" title="Theme: auto" aria-label="Theme"></button>
  </div>
</header>
<div id="layout">
{_outline(turns)}
<main id="content">
<h1>{title}{archived}</h1>
<details class="meta-box"><summary>Details &middot; {_esc(provider_label)} &middot; {_esc(project_path)}</summary>
{_meta_table(conv, project_path, provider_label)}</details>
<div id="conv-main">{body_html}</div>
{subs_html}
<footer>Exported from My AI Code Viewer{version} on {_esc(when)}</footer>
</main>
</div>
<script>{_JS}</script>
</body>
</html>
"""


_CSS = r"""
:root{--bg:#ffffff;--fg:#1f2328;--muted:#6e7781;--border:#d0d7de;--user:#ddf4ff;--assistant:#f6f8fa;
--tool:#fff8e6;--thinking:#f3eefc;--error:#ffebe9;--link:#0969da;--code:#eaeef2;--bar:rgba(255,255,255,.88);
--hit:#fff1a8;--hit-cur:#ffb02e;--active:#ddf4ff;--bar-h:56px;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1e2228;--fg:#e6edf3;--muted:#8b949e;
--border:#30363d;--user:#12324a;--assistant:#262c36;--tool:#3a3220;--thinking:#2e2640;--error:#4a1f22;
--link:#58a6ff;--code:#161b22;--bar:rgba(30,34,40,.88);--hit:#6b5a00;--hit-cur:#b26b00;--active:#12324a;
color-scheme:dark}}
:root[data-theme="dark"]{--bg:#1e2228;--fg:#e6edf3;--muted:#8b949e;--border:#30363d;--user:#12324a;
--assistant:#262c36;--tool:#3a3220;--thinking:#2e2640;--error:#4a1f22;--link:#58a6ff;--code:#161b22;
--bar:rgba(30,34,40,.88);--hit:#6b5a00;--hit-cur:#b26b00;--active:#12324a;color-scheme:dark}
*{box-sizing:border-box}
html{scroll-padding-top:calc(var(--bar-h) + 12px)}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
a{color:var(--link)}
code,pre{font-family:ui-monospace,SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;font-size:13px}
#bar{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;
padding:8px 16px;background:var(--bar);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
border-bottom:1px solid var(--border)}
.bar-title{font-weight:600;flex:1 1 200px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-opts{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13.5px}
.bar-opts label{display:inline-flex;align-items:center;gap:5px;cursor:pointer;user-select:none;white-space:nowrap}
.bar-opts label:has(input:disabled){opacity:.5;cursor:default}
.bar-tools{display:flex;align-items:center;gap:8px}
.search{display:flex;align-items:center;gap:4px;border:1px solid var(--border);border-radius:8px;
padding:2px 4px 2px 8px;background:var(--bg)}
.search input{border:0;outline:0;background:transparent;color:var(--fg);width:170px;font:inherit;font-size:13.5px}
#qcount{font-size:12px;color:var(--muted);min-width:3.5em;text-align:right;white-space:nowrap}
button{font:inherit;color:var(--fg);background:transparent;border:1px solid transparent;border-radius:6px;
cursor:pointer;padding:2px 6px}
button:hover{border-color:var(--border);background:var(--assistant)}
#theme{display:inline-flex;align-items:center;padding:4px}
#theme svg{width:18px;height:18px;display:block}
#layout{display:flex;align-items:flex-start}
#outline{display:none;position:sticky;top:var(--bar-h);height:calc(100vh - var(--bar-h));overflow:auto;
width:310px;flex:none;border-right:1px solid var(--border);padding:8px 0;font-size:13.5px}
body.show-outline #outline{display:block}
#outline ol{list-style:none;margin:0;padding:0}
#outline li{padding:6px 12px;border-left:3px solid transparent}
#outline li.active{background:var(--active);border-left-color:var(--link)}
#outline a{display:block;text-decoration:none;color:var(--fg);overflow:hidden;text-overflow:ellipsis;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
#outline a.o-prompt{font-weight:600}
#outline .o-answer{color:var(--muted);font-size:12.5px;-webkit-line-clamp:1}
#outline .o-answer:hover{color:var(--link)}
.o-no{color:var(--muted);font-weight:400}
#content{flex:1;min-width:0;max-width:1000px;margin:0 auto;padding:12px 28px 80px}
h1{font-size:22px;margin:12px 0 6px;line-height:1.3;overflow-wrap:anywhere}
.tag{font-size:12px;font-weight:500;color:var(--muted);border:1px solid var(--border);border-radius:10px;
padding:1px 8px;margin-left:10px;vertical-align:middle}
.meta-box{margin:0 0 18px;color:var(--muted);font-size:13px}
.meta-box>summary{cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
table.meta{border-collapse:collapse;margin:8px 0 4px;font-size:13px;color:var(--fg)}
table.meta th{text-align:left;font-weight:400;color:var(--muted);padding:2px 14px 2px 0;vertical-align:top;
white-space:nowrap}
table.meta td{padding:2px 0;overflow-wrap:anywhere}
.blk{margin:0 0 8px}
.hdr{display:flex;align-items:baseline;gap:8px;margin:18px 0 6px;font-size:13.5px}
body:not(.qa-only) .hdr:not(.h-full),body.qa-only .hdr:not(.h-qa),body.qa-only .nonqa{display:none}
.role{font-weight:600}
.when{color:var(--muted);font-size:12px}
.turn-no{font-weight:600;text-decoration:none}
.bubble{border-radius:10px;padding:10px 14px;overflow-wrap:anywhere;position:relative}
.bubble.user{background:var(--user)}
.bubble.assistant{background:var(--assistant)}
.bubble.error{background:var(--error)}
.prose{white-space:pre-wrap}
.answer-badge{float:right;margin:-2px -4px 4px 10px;font-size:11.5px;color:var(--muted);text-decoration:none;
border:1px solid var(--border);border-radius:10px;padding:0 8px}
.answer-badge:hover{color:var(--link)}
body:not(.md-off) .alt-plain{display:none}
body.md-off .md{display:none}
.md>:first-child,.alt-plain>:first-child{margin-top:0}
.md>:last-child,.alt-plain>:last-child{margin-bottom:0}
.md h1,.md h2,.md h3,.md h4,.md h5,.md h6{margin:14px 0 6px;line-height:1.3}
.md h1{font-size:19px}.md h2{font-size:17px}.md h3{font-size:15.5px}.md h4,.md h5,.md h6{font-size:15px}
.md p,.md ul,.md ol,.md blockquote,.md table{margin:8px 0}
.md ul,.md ol{padding-left:24px}
.md blockquote{border-left:3px solid var(--border);margin-left:0;padding-left:12px;color:var(--muted)}
.md table{border-collapse:collapse;display:block;overflow-x:auto}
.md th,.md td{border:1px solid var(--border);padding:4px 9px}
.md :not(pre)>code{background:var(--code);border-radius:4px;padding:1px 5px}
pre{background:var(--code);border-radius:8px;padding:10px 12px;margin:8px 0;overflow-x:auto;line-height:1.45}
pre code{background:none;padding:0}
.codewrap{position:relative}
.codewrap .copy{position:absolute;top:6px;right:6px;font-size:12px;padding:1px 8px;opacity:0;
background:var(--bg);border-color:var(--border);transition:opacity .15s}
.codewrap:hover .copy,.copy:focus{opacity:1}
@media (hover:none){.codewrap .copy{opacity:.85}}
details.fold{border-radius:8px;padding:5px 12px;margin:0}
details.fold.tool{background:var(--tool)}
details.fold.tool.err{background:var(--error)}
details.fold.thinking{background:var(--thinking)}
details>summary{cursor:pointer;list-style:none}
details>summary::-webkit-details-marker{display:none}
details.fold>summary,details.subagent>summary{display:flex;gap:8px;align-items:baseline;min-width:0}
details.fold>summary::before,details.subagent>summary::before{content:"\25B8";color:var(--muted);flex:none;
transition:transform .12s}
details[open].fold>summary::before,details[open].subagent>summary::before{transform:rotate(90deg)}
details.fold>summary b{flex:none;font-weight:600;font-size:13.5px}
.preview{color:var(--muted);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
details[open]>summary .hide-open{display:none}
.fold-body{padding:6px 0 4px}
.fold-body pre{background:var(--bg);margin:2px 0}
#subagents{margin-top:36px;border-top:1px solid var(--border);padding-top:8px}
#subagents h2{font-size:17px}
details.subagent{border:1px solid var(--border);border-radius:10px;padding:8px 14px;margin:0 0 10px}
.sub-body{margin-top:6px}
mark.hit{background:var(--hit);color:inherit;border-radius:2px}
mark.hit.cur{background:var(--hit-cur)}
footer{margin-top:48px;color:var(--muted);font-size:12px;text-align:center}
.muted{color:var(--muted)}
@media (max-width:820px){
#outline{position:fixed;left:0;top:var(--bar-h);z-index:15;background:var(--bg);width:min(86vw,320px);
box-shadow:4px 0 18px rgba(0,0,0,.18)}
#content{padding:10px 16px 60px}
.search input{width:110px}
}
@media print{#bar,#outline,.copy{display:none!important}details.fold{break-inside:avoid}}
"""

_JS = r"""
(function () {
  var body = document.body, root = document.documentElement;
  var bar = document.getElementById('bar'), outline = document.getElementById('outline');
  var main = document.getElementById('conv-main');

  function setBarHeight() { root.style.setProperty('--bar-h', bar.offsetHeight + 'px'); }
  setBarHeight();
  window.addEventListener('resize', setBarHeight);

  /* ---------- theme ---------- */
  var ICONS = {
    auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
      + 'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    dark: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'
  };
  var themes = ['auto', 'light', 'dark'], themeBtn = document.getElementById('theme');
  function applyTheme(t) {
    if (t === 'auto') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', t);
    themeBtn.title = 'Theme: ' + t;
    themeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round">' + ICONS[t] + '</svg>';
  }
  var theme = body.getAttribute('data-default-theme') || 'auto';
  try { theme = localStorage.getItem('aicv-theme') || theme; } catch (e) {}
  if (themes.indexOf(theme) < 0) theme = 'auto';
  applyTheme(theme);
  themeBtn.addEventListener('click', function () {
    theme = themes[(themes.indexOf(theme) + 1) % themes.length];
    applyTheme(theme);
    try { localStorage.setItem('aicv-theme', theme); } catch (e) {}
  });

  /* ---------- turns + outline scroll-spy ---------- */
  var turnEls = Array.prototype.slice.call(main.querySelectorAll('.blk[id^="turn-"]'));
  function currentTurn() {
    var limit = bar.offsetHeight + 40, cur = null;
    for (var i = 0; i < turnEls.length; i++) {
      if (turnEls[i].getBoundingClientRect().top <= limit) cur = turnEls[i]; else break;
    }
    return cur || turnEls[0] || null;
  }
  var activeLi = null;
  function spy() {
    if (!body.classList.contains('show-outline')) return;
    var t = currentTurn();
    var li = t ? outline.querySelector('li[data-turn="' + t.id.slice(5) + '"]') : null;
    if (li === activeLi) return;
    if (activeLi) activeLi.classList.remove('active');
    activeLi = li;
    if (li) {
      li.classList.add('active');
      var top = li.offsetTop - outline.scrollTop;
      if (top < 0 || top + li.offsetHeight > outline.clientHeight)
        outline.scrollTop = li.offsetTop - outline.clientHeight / 3;
    }
  }
  var spyQueued = false;
  window.addEventListener('scroll', function () {
    if (spyQueued) return;
    spyQueued = true;
    requestAnimationFrame(function () { spyQueued = false; spy(); });
  }, {passive: true});
  outline.addEventListener('click', function (e) {
    if (e.target.closest('a') && window.matchMedia('(max-width: 820px)').matches) {
      body.classList.remove('show-outline');
      var cb = document.querySelector('input[data-opt="outline"]');
      if (cb) cb.checked = false;
    }
  });

  // On a phone the outline is an overlay - don't open the page covered by it.
  if (window.matchMedia('(max-width: 820px)').matches && body.classList.contains('show-outline')) {
    body.classList.remove('show-outline');
    var ocb = document.querySelector('input[data-opt="outline"]');
    if (ocb) ocb.checked = false;
  }

  /* ---------- option toggles ---------- */
  document.querySelectorAll('input[data-opt]').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var opt = cb.dataset.opt, on = cb.checked, anchor = currentTurn();
      if (opt === 'outline') body.classList.toggle('show-outline', on);
      else if (opt === 'qa') body.classList.toggle('qa-only', on);
      else if (opt === 'markdown') body.classList.toggle('md-off', !on);
      else if (opt === 'tools') main.parentNode.querySelectorAll('details.tool').forEach(function (d) { d.open = on; });
      else if (opt === 'thinking') main.parentNode.querySelectorAll('details.thinking').forEach(function (d) { d.open = on; });
      // Keep the reader on the same turn when the page reflows a lot.
      if ((opt === 'qa' || opt === 'markdown') && anchor) anchor.scrollIntoView();
      setBarHeight();
      activeLi = null;
      spy();
      if (query) runSearch(true);
    });
  });

  /* ---------- copy buttons on code ---------- */
  document.querySelectorAll('#content pre').forEach(function (pre) {
    var wrap = document.createElement('div');
    wrap.className = 'codewrap';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copy';
    btn.textContent = 'Copy';
    btn.addEventListener('click', function () {
      var text = pre.innerText;
      function done(ok) {
        btn.textContent = ok ? 'Copied' : 'Copy failed';
        setTimeout(function () { btn.textContent = 'Copy'; }, 1400);
      }
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(ta);
        done(ok);
      }
      if (navigator.clipboard && window.isSecureContext)
        navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
      else fallback();
    });
    wrap.appendChild(btn);
  });

  /* ---------- search ---------- */
  var input = document.getElementById('q'), countEl = document.getElementById('qcount');
  var content = document.getElementById('content');
  var query = '', allMarks = [], marks = [], cur = -1, MAX_HITS = 5000;

  function hiddenByMode(el) {
    if (body.classList.contains('qa-only') && el.closest('.nonqa')) return true;
    if (body.classList.contains('md-off') ? el.closest('.md') : el.closest('.alt-plain')) return true;
    return false;
  }
  function clearMarks() {
    allMarks.forEach(function (m) {
      var p = m.parentNode;
      if (!p) return;
      p.replaceChild(document.createTextNode(m.textContent), m);
      p.normalize();
    });
    allMarks = []; marks = []; cur = -1;
  }
  function highlight(q) {
    var lower = q.toLowerCase(), nodes = [];
    var walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var p = n.parentNode;
        if (!p || p.closest('button, footer, script, style')) return NodeFilter.FILTER_REJECT;
        return n.nodeValue.toLowerCase().indexOf(lower) >= 0 ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (var i = 0; i < nodes.length && allMarks.length < MAX_HITS; i++) {
      var node = nodes[i], text = node.nodeValue, low = text.toLowerCase(), pos = 0, idx;
      var frag = document.createDocumentFragment();
      while ((idx = low.indexOf(lower, pos)) >= 0 && allMarks.length < MAX_HITS) {
        if (idx > pos) frag.appendChild(document.createTextNode(text.slice(pos, idx)));
        var m = document.createElement('mark');
        m.className = 'hit';
        m.textContent = text.slice(idx, idx + q.length);
        frag.appendChild(m);
        allMarks.push(m);
        pos = idx + q.length;
      }
      if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
      node.parentNode.replaceChild(frag, node);
    }
  }
  function showCount() {
    countEl.textContent = !query ? '' : marks.length ? (cur + 1) + '/' + marks.length
      + (allMarks.length >= MAX_HITS ? '+' : '') : 'none';
  }
  function goTo(i) {
    if (!marks.length) { showCount(); return; }
    if (cur >= 0 && marks[cur]) marks[cur].classList.remove('cur');
    cur = (i + marks.length) % marks.length;
    var m = marks[cur];
    m.classList.add('cur');
    for (var d = m.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
    m.scrollIntoView({block: 'center'});
    showCount();
  }
  function runSearch(keepPlace) {
    var q = input.value.trim();
    if (q !== query || !keepPlace) {
      clearMarks();
      query = q;
      if (q.length >= 2 || (q.length === 1 && /[^\x00-\x7f]/.test(q))) highlight(q);
    }
    marks = allMarks.filter(function (m) { return !hiddenByMode(m); });
    allMarks.forEach(function (m) { m.classList.remove('cur'); });
    cur = -1;
    if (!keepPlace) goTo(0); else showCount();
  }
  var timer = null;
  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () { runSearch(false); }, 220);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      clearTimeout(timer);
      if (input.value.trim() !== query) runSearch(false); else goTo(cur + (e.shiftKey ? -1 : 1));
    } else if (e.key === 'Escape') {
      input.value = ''; runSearch(false); input.blur();
    }
  });
  document.getElementById('qnext').addEventListener('click', function () { goTo(cur + 1); });
  document.getElementById('qprev').addEventListener('click', function () { goTo(cur - 1); });
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if (e.key === '/' && !(t.matches && t.matches('input, textarea')) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault(); input.focus(); input.select();
    }
  });

  spy();
})();
"""
