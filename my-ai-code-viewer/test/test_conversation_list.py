from app.conversation_list import arrange, default_ascending, header_label, title_matches
from app.models import Conversation


def conv(ext, title, prompts=0, tokens=0, cost=None, updated="2026-01-01", parent=None):
    return Conversation(
        id=hash(ext) & 0xFFFF, project_id=1, provider="claude-code", external_id=ext, parent_external_id=parent,
        title=title, first_prompt=None, model=None, cwd=None, git_branch=None, started_at=updated,
        updated_at=updated, message_count=0, user_message_count=prompts, tool_call_count=0,
        input_tokens=tokens, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, cost_usd=cost,
        tools_used={}, source_path=None, missing_from_source=False)


def titles(rows):
    return [(top.title, [k.title for k in kids]) for top, kids in rows]


def test_title_matches_all_words_any_order_case_insensitive():
    assert title_matches("Login bug fix", "FIX login")
    assert not title_matches("Login bug fix", "fix signup")
    assert title_matches("anything", "   ")


def test_numeric_sort_is_numeric_not_textual():
    convs = [conv("a", "A", prompts=9), conv("b", "B", prompts=10), conv("c", "C", prompts=2)]
    assert [t for t, _ in titles(arrange(convs, "", 3, False))] == ["B", "A", "C"]
    assert [t for t, _ in titles(arrange(convs, "", 3, True))] == ["C", "A", "B"]


def test_cost_sort_treats_missing_as_zero():
    convs = [conv("a", "A", cost=None), conv("b", "B", cost=0.5)]
    assert [t for t, _ in titles(arrange(convs, "", 6, False))] == ["B", "A"]


def test_children_sorted_under_parent_and_filter_keeps_family():
    convs = [conv("p", "Parent refactor"), conv("q", "Other"),
             conv("k1", "explore tests", tokens=5, parent="p"), conv("k2", "write docs", tokens=50, parent="p")]
    assert titles(arrange(convs, "", 5, False))[0] == ("Parent refactor", ["write docs", "explore tests"])
    # matching parent shows all children
    assert titles(arrange(convs, "refactor", 5, False)) == [("Parent refactor", ["write docs", "explore tests"])]
    # matching child keeps its parent, hides non-matching siblings
    assert titles(arrange(convs, "docs", 5, False)) == [("Parent refactor", ["write docs"])]


def test_orphan_subagent_is_top_level():
    rows = arrange([conv("k", "lonely", parent="gone")], "", 0, True)
    assert titles(rows) == [("lonely", [])]


def test_first_click_direction_and_header_arrow():
    assert default_ascending(0) is True      # title A-Z
    assert default_ascending(2) is False     # newest first
    assert default_ascending(6) is False     # most expensive first
    assert header_label(6, 6, False) == "Cost ▼"
    assert header_label(3, 6, False) == "Prompts"
