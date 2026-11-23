import os

from conftest import CWD, PROJECT_KEY

from app.indexer import sync
from app.providers.claude_code import ClaudeCodeProvider
from app.providers.opencode import OpenCodeProvider


def test_first_sync_indexes_everything(index_repo, claude_root, opencode_db):
    result = sync(index_repo, [ClaudeCodeProvider(claude_root), OpenCodeProvider(opencode_db)])

    assert (result.scanned, result.updated, result.missing, result.errors) == (3, 3, 0, [])
    groups = index_repo.list_project_groups()
    assert len(groups) == 1  # same folder used by both tools -> one project
    group = groups[0]
    assert group.path == CWD
    assert sorted(group.providers) == ["claude-code", "opencode"]
    assert group.conversation_count == 2  # subagent threads aren't top-level conversations
    convs = index_repo.list_conversations(group.project_ids)
    assert {c.external_id for c in convs} == {"s1", "s1:agent-abc", "ses_1"}
    s1 = next(c for c in convs if c.external_id == "s1")
    assert len(index_repo.list_messages(s1.id)) == 5
    assert s1.tools_used == {"Bash": 1}


def test_resync_skips_unchanged_and_reparses_changed(index_repo, claude_root):
    provider = ClaudeCodeProvider(claude_root)
    sync(index_repo, [provider])

    assert sync(index_repo, [provider]).updated == 0

    transcript = claude_root / PROJECT_KEY / "s1.jsonl"
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write('{"type":"user","uuid":"u9","cwd":"%s","timestamp":"2026-01-02T00:00:00.000Z",'
                 '"message":{"role":"user","content":"one more"}}\n' % CWD)
    st = transcript.stat()
    os.utime(transcript, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    assert sync(index_repo, [provider]).updated == 1
    conv = next(c for c in index_repo.list_conversations(
        [g for g in index_repo.list_project_groups()][0].project_ids) if c.external_id == "s1")
    assert conv.message_count == 6
    assert conv.updated_at == "2026-01-02T00:00:00.000Z"


def test_deleted_transcript_stays_archived(index_repo, claude_root):
    provider = ClaudeCodeProvider(claude_root)
    sync(index_repo, [provider])

    (claude_root / PROJECT_KEY / "s1.jsonl").unlink()
    result = sync(index_repo, [provider])

    assert result.missing == 1
    group = index_repo.list_project_groups()[0]
    conv = next(c for c in index_repo.list_conversations(group.project_ids) if c.external_id == "s1")
    assert conv.missing_from_source is True
    assert len(index_repo.list_messages(conv.id)) == 5  # still readable


def test_unavailable_provider_is_skipped(index_repo, tmp_path):
    result = sync(index_repo, [ClaudeCodeProvider(tmp_path / "missing")])
    assert (result.scanned, result.updated) == (0, 0)


def test_project_filters(index_repo, claude_root, opencode_db):
    sync(index_repo, [ClaudeCodeProvider(claude_root), OpenCodeProvider(opencode_db)])

    assert [g.providers for g in index_repo.list_project_groups(provider="opencode")] == [["opencode"]]
    assert index_repo.list_project_groups(text_filter="my-app")
    assert index_repo.list_project_groups(text_filter="zzz") == []
