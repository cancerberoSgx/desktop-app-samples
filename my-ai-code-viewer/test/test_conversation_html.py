from app.conversation_html import render_conversation_html, tool_summary
from app.indexer import sync
from app.models import MessageBlock
from app.providers.claude_code import ClaudeCodeProvider


def _render(index_repo, claude_root, **kwargs):
    sync(index_repo, [ClaudeCodeProvider(claude_root)])
    group = index_repo.list_project_groups()[0]
    conv = next(c for c in index_repo.list_conversations(group.project_ids) if c.external_id == "s1")
    blocks = index_repo.list_messages(conv.id)
    opts = dict(project_path=group.path, provider_label="Claude Code", expanded=set(),
                expand_tools=False, expand_thinking=False)
    opts.update(kwargs)
    return render_conversation_html(conv, blocks, **opts)


def test_tools_collapsed_by_default(index_repo, claude_root):
    page = _render(index_repo, claude_root)
    assert 'href="toggle:2"' in page
    assert "Tool: Bash" in page
    assert "<pre>{\n" not in page  # tool input body hidden
    assert "list the files" in page and "Two files" in page


def test_clicked_block_expands(index_repo, claude_root):
    page = _render(index_repo, claude_root, expanded={2})
    assert "&quot;command&quot;: &quot;ls -la&quot;" in page


def test_html_is_escaped(index_repo, claude_root):
    page = _render(index_repo, claude_root)
    assert "<script" not in page


def test_tool_summary_prefers_meaningful_field():
    block = MessageBlock("assistant", "tool_use", '{"command": "git status", "timeout": 5}', tool_name="Bash")
    assert tool_summary(block) == "git status"


def test_markdown_toggle_affects_assistant_and_thinking_not_user(index_repo, claude_root):
    sync(index_repo, [ClaudeCodeProvider(claude_root)])
    group = index_repo.list_project_groups()[0]
    conv = next(c for c in index_repo.list_conversations(group.project_ids) if c.external_id == "s1")
    blocks = [MessageBlock("user", "text", "**mine**", seq=0),
              MessageBlock("assistant", "thinking", "**pondering**", seq=1),
              MessageBlock("assistant", "text", "**reply**", seq=2)]
    opts = dict(project_path=group.path, provider_label="Claude Code", expanded=set(),
                expand_tools=False, expand_thinking=True)
    on = render_conversation_html(conv, blocks, render_markdown=True, **opts)
    assert "**mine**" in on
    assert "<strong>pondering</strong>" in on and "<strong>reply</strong>" in on
    off = render_conversation_html(conv, blocks, render_markdown=False, **opts)
    assert "<strong>" not in off and "**reply**" in off
