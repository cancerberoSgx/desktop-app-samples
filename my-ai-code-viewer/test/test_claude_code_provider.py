from conftest import CWD, PROJECT_KEY, claude_session_lines, write_jsonl

from app.providers.claude_code import ClaudeCodeProvider, encode_path, naive_decode


def test_encode_path_matches_claude_folder_naming():
    assert encode_path("/home/me/my-app.v2") == PROJECT_KEY
    assert naive_decode("-home-me-app") == "/home/me/app"


def test_list_sources_finds_sessions_and_subagents(claude_root):
    sources = ClaudeCodeProvider(claude_root).list_sources()
    by_id = {s.external_id: s for s in sources}

    assert set(by_id) == {"s1", "s1:agent-abc"}
    assert by_id["s1"].project_key == PROJECT_KEY
    assert by_id["s1:agent-abc"].parent_external_id == "s1"
    assert by_id["s1:agent-abc"].title_hint == "general-purpose: Review code"


def test_parse_normalizes_blocks_and_metadata(claude_root):
    provider = ClaudeCodeProvider(claude_root)
    source = next(s for s in provider.list_sources() if s.external_id == "s1")

    conv = provider.parse(source)

    assert conv.project_path == CWD  # real path recovered despite lossy folder name
    assert conv.title == "List files"
    assert conv.first_prompt == "list the files"
    assert conv.model == "claude-opus-5-5"
    assert conv.git_branch == "main"
    assert conv.cost_usd == 0.42
    assert conv.started_at == "2026-01-01T10:00:00.000Z"
    assert conv.updated_at == "2026-01-01T10:00:04.000Z"
    assert [(b.role, b.kind) for b in conv.blocks] == [
        ("user", "text"), ("assistant", "thinking"), ("assistant", "tool_use"),
        ("user", "tool_result"), ("assistant", "text"),
    ]
    assert conv.blocks[3].tool_name == "Bash"  # result linked back to its call
    assert [b.seq for b in conv.blocks] == list(range(5))
    assert conv.tools_used == {"Bash": 1}
    # usage counted once per API message id (last line wins), not per line
    assert conv.input_tokens == 11
    assert conv.output_tokens == 27
    assert conv.cache_read_tokens == 100
    assert conv.cache_write_tokens == 50


def test_parse_subagent_uses_meta_description_as_title(claude_root):
    provider = ClaudeCodeProvider(claude_root)
    source = next(s for s in provider.list_sources() if s.parent_external_id)

    conv = provider.parse(source)

    assert conv.parent_external_id == "s1"
    assert conv.title == "general-purpose: Review code"
    assert [b.text for b in conv.blocks] == ["review the code", "Looks fine."]


def test_first_prompt_falls_back_when_all_prompts_are_markup(tmp_path):
    lines = [{"type": "user", "uuid": "u", "cwd": CWD, "timestamp": "2026-01-01T00:00:00Z",
              "message": {"role": "user", "content": "<pasted_content id='x'>fix this</pasted_content>"}}]
    write_jsonl(tmp_path / PROJECT_KEY / "s.jsonl", lines)
    provider = ClaudeCodeProvider(tmp_path)

    conv = provider.parse(provider.list_sources()[0])

    assert conv.first_prompt == "fix this"


def test_unknown_project_path_falls_back_to_naive_decode(tmp_path):
    write_jsonl(tmp_path / "-somewhere-else" / "s.jsonl", claude_session_lines(cwd="/elsewhere"))
    provider = ClaudeCodeProvider(tmp_path)

    conv = provider.parse(provider.list_sources()[0])

    assert conv.project_path == "/somewhere/else"


def test_missing_root_is_unavailable(tmp_path):
    provider = ClaudeCodeProvider(tmp_path / "nope")
    assert not provider.is_available()
    assert provider.list_sources() == []
