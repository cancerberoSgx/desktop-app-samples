import json
import sqlite3
from pathlib import Path

from conftest import CWD, write_jsonl

from app.providers.codex import INTERRUPTED, CodexProvider


def line(ts, kind, payload):
    return {"timestamp": ts, "type": kind, "payload": payload}


def rollout_lines(thread_id="t1", cwd=CWD, source: object = "cli"):
    return [
        line("2026-01-01T10:00:00.000Z", "session_meta",
             {"id": thread_id, "timestamp": "2026-01-01T10:00:00.000Z", "cwd": cwd, "source": source,
              "git": {"branch": "main"}}),
        line("2026-01-01T10:00:00.100Z", "response_item",
             {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "sandbox rules"}]}),
        line("2026-01-01T10:00:00.100Z", "response_item",
             {"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "<environment_context>\n<cwd>x</cwd>"}]}),
        line("2026-01-01T10:00:00.200Z", "response_item",
             {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list the files"}]}),
        line("2026-01-01T10:00:00.200Z", "event_msg", {"type": "user_message", "message": "list the files"}),
        line("2026-01-01T10:00:00.300Z", "turn_context", {"cwd": cwd, "model": "gpt-5.2-codex"}),
        line("2026-01-01T10:00:01.000Z", "response_item",
             {"type": "reasoning", "summary": [{"type": "summary_text", "text": "**Looking**"}],
              "encrypted_content": "xxx"}),
        line("2026-01-01T10:00:01.100Z", "response_item",
             {"type": "function_call", "name": "shell_command", "call_id": "c1",
              "arguments": json.dumps({"command": "ls", "workdir": cwd})}),
        line("2026-01-01T10:00:01.200Z", "response_item",
             {"type": "function_call_output", "call_id": "c1", "output": "Exit code: 0\nOutput:\na.py\n"}),
        line("2026-01-01T10:00:01.300Z", "response_item",
             {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2", "input": "*** Begin Patch"}),
        line("2026-01-01T10:00:01.400Z", "response_item",
             {"type": "custom_tool_call_output", "call_id": "c2",
              "output": json.dumps({"output": "patch failed", "metadata": {"exit_code": 1}})}),
        line("2026-01-01T10:00:02.000Z", "response_item",
             {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]}),
        line("2026-01-01T10:00:02.000Z", "event_msg", {"type": "agent_message", "message": "Done."}),
        line("2026-01-01T10:00:02.100Z", "event_msg",
             {"type": "token_count", "info": {"total_token_usage": {
                 "input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50,
                 "reasoning_output_tokens": 20, "total_tokens": 1050}}}),
        line("2026-01-01T10:00:03.000Z", "event_msg", {"type": "user_message", "message": "now test it"}),
        line("2026-01-01T10:00:04.000Z", "event_msg", {"type": "turn_aborted", "reason": "interrupted"}),
    ]


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex"
    write_jsonl(home / "sessions/2026/01/01/rollout-2026-01-01T10-00-00-t1.jsonl", rollout_lines())
    return home


def test_list_sources(tmp_path):
    home = make_home(tmp_path)
    # legacy TypeScript-CLI sessions have no cwd and are skipped
    (home / "sessions/rollout-2025-06-09-old.json").write_text('{"session": {}, "items": []}')

    sources = CodexProvider(home).list_sources()

    assert [(s.external_id, s.project_key, s.parent_external_id) for s in sources] == [("t1", CWD, None)]


def test_parse_normalizes_rollout(tmp_path):
    provider = CodexProvider(make_home(tmp_path))

    conv = provider.parse(provider.list_sources()[0])

    assert conv.first_prompt == "list the files"
    assert conv.model == "gpt-5.2-codex"
    assert conv.git_branch == "main"
    assert conv.cwd == CWD
    assert conv.started_at == "2026-01-01T10:00:00.000Z"
    assert conv.updated_at == "2026-01-01T10:00:04.000Z"
    assert (conv.input_tokens, conv.cache_read_tokens, conv.output_tokens) == (200, 800, 50)
    assert conv.cost_usd is None
    # user prompts come from user_message events only (no duplicates, no injected context)
    assert [(b.role, b.kind, b.tool_name, b.is_error, b.text) for b in conv.blocks] == [
        ("user", "text", None, False, "list the files"),
        ("assistant", "thinking", None, False, "**Looking**"),
        ("assistant", "tool_use", "shell_command", False, "ls"),
        ("assistant", "tool_result", "shell_command", False, "Exit code: 0\nOutput:\na.py\n"),
        ("assistant", "tool_use", "apply_patch", False, "*** Begin Patch"),
        ("assistant", "tool_result", "apply_patch", True, "patch failed"),
        ("assistant", "text", None, False, "Done."),
        ("user", "text", None, False, "now test it"),
        ("user", "text", None, False, INTERRUPTED),
    ]
    assert conv.tools_used == {"shell_command": 1, "apply_patch": 1}


def test_old_rollout_without_user_message_events(tmp_path):
    home = tmp_path / "codex"
    lines = [l for l in rollout_lines() if l["payload"].get("type") != "user_message"]
    write_jsonl(home / "sessions/2026/01/01/rollout-x-t1.jsonl", lines)
    provider = CodexProvider(home)

    conv = provider.parse(provider.list_sources()[0])

    prompts = [b.text for b in conv.blocks if b.role == "user"]
    assert prompts == ["list the files", INTERRUPTED]


def test_state_db_names_and_subagents(tmp_path):
    home = make_home(tmp_path)
    write_jsonl(home / "sessions/2026/01/01/rollout-x-t2.jsonl", rollout_lines("t2"))
    write_jsonl(home / "archived_sessions/rollout-x-t3.jsonl", rollout_lines(
        "t3", source={"subagent": {"thread_spawn": {"parent_thread_id": "t1", "depth": 1}}}))
    db = sqlite3.connect(home / "state_5.sqlite")
    db.executescript("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT);"
                     "CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT);"
                     "INSERT INTO threads VALUES ('t1', 'Renamed'), ('t2', NULL);"
                     "INSERT INTO thread_spawn_edges VALUES ('t1', 't2', 'done');")
    db.commit()
    db.close()
    provider = CodexProvider(home)

    sources = {s.external_id: s for s in provider.list_sources()}

    assert sources["t1"].title_hint == "Renamed"
    assert sources["t2"].parent_external_id == "t1"   # from thread_spawn_edges
    assert sources["t3"].parent_external_id == "t1"   # from session_meta.source
    assert provider.parse(sources["t1"]).title == "Renamed"


def test_missing_home_is_unavailable(tmp_path):
    provider = CodexProvider(tmp_path / "nope")
    assert not provider.is_available()
    assert provider.list_sources() == []
