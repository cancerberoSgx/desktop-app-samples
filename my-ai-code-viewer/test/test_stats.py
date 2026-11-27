from datetime import date, datetime, timezone

from app.conversation_turns import split_turns
from app.indexer import sync
from app.models import MessageBlock, ParsedConversation
from app.providers.claude_code import ClaudeCodeProvider
from app.providers.opencode import OpenCodeProvider
from app.stats import QA_BUCKETS, load_stats, since_for

LABELS = {"claude-code": "Claude Code", "opencode": "OpenCode"}


def _blocks(*specs):
    return [MessageBlock(role, kind, text, tool_name=tool, timestamp=ts, is_error=err, seq=i)
            for i, (role, kind, text, tool, ts, err) in enumerate(specs)]


def _save(repo, ext_id, blocks, provider="claude-code", path="/p/a", parent=None, updated="2026-03-01T12:00:00Z",
          cost=None, tools=None, **tokens):
    project_id = repo.upsert_project(provider, path, path)
    conv = ParsedConversation(external_id=ext_id, parent_external_id=parent, title=f"T {ext_id}",
                              started_at="2026-03-01T10:00:00Z", updated_at=updated, cost_usd=cost,
                              tools_used=tools or {}, blocks=blocks, **tokens)
    conv_id = repo.save_conversation(project_id, provider, f"/src/{ext_id}", "fp", conv)
    repo.commit()
    return conv_id


def test_end_to_end_from_real_providers(index_repo, conn, claude_root, opencode_db):
    sync(index_repo, [ClaudeCodeProvider(claude_root), OpenCodeProvider(opencode_db)])
    s = load_stats(conn, provider_labels=LABELS)

    assert s.total.conversations == 2              # the subagent is rolled into s1
    assert s.total.prompts == 2                    # one per session; subagent prompt isn't yours
    assert sorted(a.label for a in s.agents) == ["Claude Code", "OpenCode"]
    claude = next(a for a in s.agents if a.key == "claude-code")
    assert claude.subagents == 1
    assert claude.cost_usd == 0.42
    assert len(s.projects) == 1 and sorted(s.projects[0].providers) == ["claude-code", "opencode"]
    bash = next(t for t in s.tools if t.name == "Bash")   # "Bash" + OpenCode's "bash" merged
    assert bash.calls == 2 and bash.conversations == 2
    read = next(t for t in s.tools if t.name == "Read")
    assert read.errors == 1


def test_prompt_counting_matches_split_turns(index_repo, conn):
    blocks = _blocks(
        ("user", "text", "<command-name>/clear</command-name>", None, "2026-03-02T09:00:00Z", False),
        ("user", "text", "first prompt", None, "2026-03-02T09:00:01Z", False),   # merged with the line above
        ("assistant", "thinking", "hmm", None, None, False),
        ("assistant", "tool_use", "{}", "Bash", None, False),
        ("user", "tool_result", "boom", "Bash", None, True),
        ("assistant", "text", "answer", None, None, False),
        ("user", "text", "  \n[Request interrupted by user]", None, "2026-03-02T09:05:00Z", False),
        ("user", "text", "second prompt", None, "2026-03-02T10:00:00Z", False),
        ("assistant", "thinking", "more", None, None, False),
    )
    _save(index_repo, "c1", blocks, tools={"Bash": 1})

    s = load_stats(conn)
    c = s.conversations[0]
    assert c.prompts == len(split_turns(blocks)) == 2
    assert (c.thinking, c.tool_errors, c.interrupts) == (2, 1, 1)
    assert s.tools[0].name == "Bash" and s.tools[0].errors == 1
    assert sum(sum(row) for row in s.heatmap) == 2
    assert s.active_days == 1


def test_subagent_tokens_and_cost_roll_into_parent(index_repo, conn):
    _save(index_repo, "p", _blocks(("user", "text", "go", None, "2026-03-01T10:00:00Z", False)),
          input_tokens=10, output_tokens=5)
    _save(index_repo, "p:agent-1", _blocks(("user", "text", "sub task", None, "2026-03-01T10:01:00Z", False),
                                           ("assistant", "thinking", "x", None, None, False)),
          parent="p", input_tokens=100, output_tokens=50, tools={"Read": 3})

    s = load_stats(conn)
    assert len(s.conversations) == 1
    c = s.conversations[0]
    assert (c.prompts, c.thinking, c.subagents, c.tool_calls) == (1, 1, 1, 0)
    assert (c.input_tokens, c.output_tokens) == (110, 55)
    assert c.cost_usd is None and s.total.cost_unknown == 1
    assert s.tools[0].name == "Read" and s.tools[0].calls == 3


def test_range_and_provider_filters(index_repo, conn):
    _save(index_repo, "old", _blocks(("user", "text", "a", None, "2026-01-01T10:00:00Z", False)),
          updated="2026-01-01T10:00:00Z", cost=1.0)
    _save(index_repo, "new", _blocks(("user", "text", "b", None, "2026-03-01T10:00:00Z", False)),
          updated="2026-03-01T10:00:00Z", cost=2.0)
    _save(index_repo, "oc", _blocks(("user", "text", "c", None, "2026-03-01T11:00:00Z", False)),
          provider="opencode", path="/p/b", updated="2026-03-01T11:00:00Z", cost=0.0)

    now = datetime(2026, 3, 5, tzinfo=timezone.utc)
    since = since_for(30, now)
    s = load_stats(conn, since=since, today=date(2026, 3, 5))
    assert {c.title for c in s.conversations} == {"T new", "T oc"}
    assert s.total.cost_usd == 2.0 and s.total.cost_unknown == 0
    assert len(s.days) >= 30 and s.days[-1] == date(2026, 3, 5)
    assert sum(s.daily["claude-code"]) == 1 and sum(s.daily["opencode"]) == 1

    s = load_stats(conn, provider="claude-code")
    assert {c.title for c in s.conversations} == {"T old", "T new"}
    assert [p.label for p in s.projects] == ["a"]
    assert since_for(None) is None


def test_qa_histogram_buckets(index_repo, conn):
    def prompts(n):
        out = []
        for i in range(n):
            out.append(("user", "text", f"q{i}", None, "2026-03-01T10:00:00Z", False))
            out.append(("assistant", "text", f"a{i}", None, None, False))
        return _blocks(*out)

    for ext, n in (("a", 1), ("b", 3), ("c", 4), ("d", 40)):
        _save(index_repo, ext, prompts(n))
    hist = dict(load_stats(conn).qa_histogram)
    assert list(hist) == [label for label, _ in QA_BUCKETS]
    assert (hist["1"], hist["2-3"], hist["4-7"], hist["32+"]) == (1, 1, 1, 1)


def test_project_groups_sum_tokens_and_cost(index_repo):
    blocks = _blocks(("user", "text", "go", None, "2026-03-01T10:00:00Z", False))
    _save(index_repo, "a", blocks, cost=1.5, input_tokens=10, output_tokens=5)
    _save(index_repo, "a:agent-1", blocks, parent="a", input_tokens=100, cache_read_tokens=1)
    _save(index_repo, "b", blocks, input_tokens=4)  # no cost-state line
    _save(index_repo, "o", blocks, provider="opencode", cost=0.25, output_tokens=7)
    _save(index_repo, "x", blocks, path="/p/other", output_tokens=3)

    groups = {g.path: g for g in index_repo.list_project_groups()}
    g = groups["/p/a"]
    assert g.conversation_count == 3
    assert g.total_tokens == 10 + 5 + 100 + 1 + 4 + 7
    assert g.cost_usd == 1.75 and g.cost_unknown == 1
    other = groups["/p/other"]
    assert (other.total_tokens, other.cost_usd, other.cost_unknown) == (3, None, 1)
