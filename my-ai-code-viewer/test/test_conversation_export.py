import re

from app.conversation_export import ExportOptions, render_export_html
from app.conversation_view import export_filename
from app.indexer import sync
from app.models import MessageBlock
from app.providers.claude_code import ClaudeCodeProvider


def _load(index_repo, claude_root):
    sync(index_repo, [ClaudeCodeProvider(claude_root)])
    group = index_repo.list_project_groups()[0]
    convs = index_repo.list_conversations(group.project_ids)
    main = next(c for c in convs if c.external_id == "s1")
    subs = [(c, index_repo.list_messages(c.id)) for c in convs if c.parent_external_id == "s1"]
    return group, main, index_repo.list_messages(main.id), subs


def _export(index_repo, claude_root, options=None, **kwargs):
    group, conv, blocks, subs = _load(index_repo, claude_root)
    kwargs.setdefault("subagents", subs)
    return render_export_html(conv, blocks, project_path=group.path, provider_label="Claude Code",
                              options=options or ExportOptions(), **kwargs)


def test_page_is_self_contained(index_repo, claude_root):
    page = _export(index_repo, claude_root)
    assert page.startswith("<!doctype html>")
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?:', page)
    assert "list the files" in page and "Two files: a.txt and b.txt" in page


def test_initial_options_become_body_classes_and_checkboxes(index_repo, claude_root):
    page = _export(index_repo, claude_root, ExportOptions(show_outline=True, qa_only=False, render_markdown=False))
    body = re.search(r'<body class="([^"]*)"', page).group(1).split()
    assert "show-outline" in body and "md-off" in body and "qa-only" not in body
    assert 'data-opt="outline" checked' in page and 'data-opt="qa">' in page


def test_every_block_is_present_with_qa_markers(index_repo, claude_root):
    page = _export(index_repo, claude_root)
    # tool calls are in the page (hidden by CSS in Q&A mode), collapsed by default
    assert "Tool: Bash" in page and "&quot;command&quot;: &quot;ls -la&quot;" in page
    assert '<details class="fold tool">' in page
    assert 'class="blk k-tool_use r-assistant nonqa"' in page
    assert 'id="turn-1"' in page and 'id="answer-1"' in page


def test_expand_options_and_flipped_blocks_open_details(index_repo, claude_root):
    page = _export(index_repo, claude_root, ExportOptions(expand_tools=True))
    assert '<details class="fold tool" open>' in page
    _, _, blocks, _ = _load(index_repo, claude_root)
    tool_seqs = {b.seq for b in blocks if b.kind == "tool_use"}
    page = _export(index_repo, claude_root, ExportOptions(expand_tools=True), expanded=tool_seqs)
    assert page.count('<details class="fold tool" open>') == sum(1 for b in blocks if b.kind == "tool_result")


def test_subagents_are_included(index_repo, claude_root):
    page = _export(index_repo, claude_root)
    assert "Subagents (1)" in page and "Looks fine." in page
    assert 'id="s1-turn-1"' in page


def test_text_is_escaped(index_repo, claude_root):
    group, conv, _, _ = _load(index_repo, claude_root)
    blocks = [MessageBlock("user", "text", "<script>alert(1)</script>", seq=0),
              MessageBlock("assistant", "text", "<img src=x onerror=alert(1)> [x](javascript:alert(1))", seq=1),
              MessageBlock("assistant", "tool_use", '{"command": "</pre><script>"}', tool_name="Bash", seq=2)]
    page = render_export_html(conv, blocks, project_path=group.path, provider_label="Claude Code",
                              options=ExportOptions())
    content = page.split("<body", 1)[1].rsplit("<script>", 1)[0]
    assert "<script" not in content and "<img" not in content
    assert "href=\"javascript:" not in content and "&lt;/pre&gt;&lt;script&gt;" in content


def test_markdown_rendered_and_plain_alternative(index_repo, claude_root):
    group, conv, _, _ = _load(index_repo, claude_root)
    blocks = [MessageBlock("user", "text", "q", seq=0),
              MessageBlock("assistant", "text", "# Title\n\n**bold**\n\n```py\nx = 1\n```", seq=1)]
    page = render_export_html(conv, blocks, project_path=group.path, provider_label="Claude Code",
                              options=ExportOptions())
    assert "<strong>bold</strong>" in page
    assert '<div class="alt-plain"><div class="prose"><b>Title</b>' in page
    assert "<pre><code>x = 1</code></pre>" in page


def test_export_filename():
    assert export_filename("Fix: the / bug?") == "Fix-the-bug.html"
    assert export_filename("   ") == "conversation.html"


def _project_page(index_repo, claude_root, **kwargs):
    from app.conversation_export import ProjectExportItem, render_project_export_html
    group, conv, blocks, subs = _load(index_repo, claude_root)
    other = MessageBlock("user", "text", "second <conversation>", seq=0)
    items = [ProjectExportItem(conv, blocks, "Claude Code", subagents=subs),
             ProjectExportItem(conv, [other, MessageBlock("assistant", "text", "ok", seq=1)], "OpenCode")]
    return render_project_export_html("my-app", group.path, items, options=ExportOptions(), **kwargs)


def test_project_export_has_one_section_per_conversation(index_repo, claude_root):
    seen = []
    page = _project_page(index_repo, claude_root, progress=seen.append)
    assert seen == [1, 2]
    assert "<title>my-app</title>" in page and 'class="project' in page
    assert re.search(r'<section class="conv" id="c1" data-conv="1">', page)
    assert re.search(r'<section class="conv" id="c2" data-conv="2" hidden>', page)
    # ids are prefixed per conversation (and per subagent) so they never clash
    assert 'id="c1-turn-1"' in page and 'id="c2-turn-1"' in page and 'id="c1-s1-turn-1"' in page
    assert "second &lt;conversation&gt;" in page and "Looks fine." in page


def test_project_export_sidebar_and_outlines(index_repo, claude_root):
    page = _project_page(index_repo, claude_root)
    assert 'Conversations (2)' in page and 'id="convs-btn"' in page
    assert re.search(r'<a href="#c1" data-conv="1" class="active">', page)
    assert '<a href="#c2" data-conv="2">' in page and "OpenCode" in page
    # one outline per conversation, only the first shown
    assert '<ol data-conv="1" class="active">' in page and '<ol data-conv="2">' in page
    assert 'href="#c2-turn-1"' in page


def test_single_export_has_no_conversation_list(index_repo, claude_root):
    page = _export(index_repo, claude_root)
    assert 'id="convs"' not in page and 'id="convs-btn"' not in page
    assert '<section class="conv" data-conv="1">' in page and '<ol data-conv="1" class="active">' in page
