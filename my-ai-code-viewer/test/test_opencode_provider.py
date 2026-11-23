from conftest import CWD

from app.providers.opencode import OpenCodeProvider


def test_list_sources(opencode_db):
    sources = OpenCodeProvider(opencode_db).list_sources()

    assert len(sources) == 1
    assert sources[0].external_id == "ses_1"
    assert sources[0].project_key == CWD
    assert sources[0].fingerprint == "1767261660000"


def test_parse_normalizes_parts(opencode_db):
    provider = OpenCodeProvider(opencode_db)

    conv = provider.parse(provider.list_sources()[0])

    assert conv.title is None  # OpenCode's placeholder "New session - ..." title is dropped
    assert conv.first_prompt == "build me an app"
    assert conv.model == "deepseek-v4"
    assert conv.cost_usd == 0.1
    assert conv.input_tokens == 100
    assert conv.output_tokens == 25  # output + reasoning
    assert conv.cache_read_tokens == 1000
    assert conv.started_at == "2026-01-01T10:00:00.000Z"
    assert [(b.role, b.kind, b.tool_name, b.is_error) for b in conv.blocks] == [
        ("user", "text", None, False),
        ("assistant", "thinking", None, False),
        ("assistant", "tool_use", "bash", False),
        ("assistant", "tool_result", "bash", False),
        ("assistant", "tool_use", "read", False),
        ("assistant", "tool_result", "read", True),
        ("assistant", "text", None, False),
        ("assistant", "error", None, True),
    ]
    assert conv.blocks[-1].text == "APIError: Insufficient Balance"
    assert conv.tools_used == {"bash": 1, "read": 1}


def test_missing_db_is_unavailable(tmp_path):
    provider = OpenCodeProvider(tmp_path / "nope.db")
    assert not provider.is_available()
    assert provider.list_sources() == []
