import hashlib
import json
from pathlib import Path

from conftest import CWD, write_jsonl

from app.providers.gemini import GeminiProvider

READ_CALL = {"id": "c1", "name": "read_file", "args": {"file_path": "requirements.txt"}, "status": "success",
             "result": [{"functionResponse": {"id": "c1", "name": "read_file",
                                              "response": {"output": "wxPython>=4.2.0\n"}}}]}
FAILED_CALL = {"id": "c2", "name": "run_shell_command", "args": {"command": "false"}, "status": "error",
               "result": [{"functionResponse": {"id": "c2", "name": "run_shell_command",
                                                "response": {"error": "exit 1"}}}]}
TOKENS = {"input": 1000, "output": 20, "cached": 800, "thoughts": 30, "tool": 0, "total": 1050}


def chat_lines(session_id="s1"):
    gemini_turn = {"id": "g1", "timestamp": "2026-01-01T10:00:02.000Z", "type": "gemini", "content": "",
                   "thoughts": [{"subject": "Reading", "description": "Look at requirements."}],
                   "tokens": TOKENS, "model": "gemini-3.8-flash"}
    return [
        {"sessionId": session_id, "projectHash": "abc", "startTime": "2026-01-01T10:00:00.000Z",
         "lastUpdated": "2026-01-01T10:00:00.000Z", "kind": "main"},
        {"id": "u0", "timestamp": "2026-01-01T10:00:00.000Z", "type": "user",
         "content": [{"text": "<session_context>\nThis is the Gemini CLI."}]},
        {"id": "i1", "timestamp": "2026-01-01T10:00:00.500Z", "type": "info", "content": "Update available"},
        {"id": "u1", "timestamp": "2026-01-01T10:00:01.000Z", "type": "user",
         "content": [{"text": "list the dependencies"}]},
        {"$set": {"lastUpdated": "2026-01-01T10:00:01.000Z"}},
        gemini_turn,
        # the same record re-appended once its tool calls finished
        dict(gemini_turn, toolCalls=[READ_CALL, FAILED_CALL]),
        {"id": "u2", "timestamp": "2026-01-01T10:00:03.000Z", "type": "user",
         "content": [{"functionResponse": {"id": "c1", "name": "read_file", "response": {"output": "x"}}}]},
        {"id": "g2", "timestamp": "2026-01-01T10:00:04.000Z", "type": "gemini", "content": "wxPython.",
         "thoughts": [], "tokens": TOKENS, "model": "gemini-3.8-flash"},
        {"id": "e1", "timestamp": "2026-01-01T10:00:05.000Z", "type": "error", "content": "Quota exceeded"},
        {"$set": {"lastUpdated": "2026-01-01T10:00:05.000Z", "summary": "Project dependencies"}},
    ]


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "gemini"
    project = home / "tmp" / "my-app"
    write_jsonl(project / "chats" / "session-2026-01-01T10-00-s1.jsonl", chat_lines())
    (project / ".project_root").write_text(CWD)
    return home


def test_list_sources(tmp_path):
    sources = GeminiProvider(make_home(tmp_path)).list_sources()

    assert [(s.external_id, s.project_key) for s in sources] == [("s1", CWD)]


def test_parse_normalizes_chat(tmp_path):
    provider = GeminiProvider(make_home(tmp_path))

    conv = provider.parse(provider.list_sources()[0])

    assert conv.title == "Project dependencies"
    assert conv.first_prompt == "list the dependencies"
    assert conv.model == "gemini-3.8-flash"
    assert conv.started_at == "2026-01-01T10:00:00.000Z"
    assert conv.updated_at == "2026-01-01T10:00:05.000Z"
    # tokens summed over gemini records, each id counted once
    assert (conv.input_tokens, conv.cache_read_tokens, conv.output_tokens) == (400, 1600, 100)
    assert [(b.role, b.kind, b.tool_name, b.is_error, b.text) for b in conv.blocks] == [
        ("user", "text", None, False, "list the dependencies"),
        ("assistant", "thinking", None, False, "**Reading**\n\nLook at requirements."),
        ("assistant", "tool_use", "read_file", False, '{\n  "file_path": "requirements.txt"\n}'),
        ("assistant", "tool_result", "read_file", False, "wxPython>=4.2.0\n"),
        ("assistant", "tool_use", "run_shell_command", False, '{\n  "command": "false"\n}'),
        ("assistant", "tool_result", "run_shell_command", True, "exit 1"),
        ("assistant", "text", None, False, "wxPython."),
        ("assistant", "error", None, True, "Quota exceeded"),
    ]
    assert conv.tools_used == {"read_file": 1, "run_shell_command": 1}


def test_rewind_drops_later_records(tmp_path):
    home = make_home(tmp_path)
    path = next((home / "tmp/my-app/chats").glob("*.jsonl"))
    with path.open("a") as f:
        f.write(json.dumps({"$rewindTo": "g2"}) + "\n")
    provider = GeminiProvider(home)

    conv = provider.parse(provider.list_sources()[0])

    assert conv.blocks[-1].tool_name == "run_shell_command"


def test_legacy_json_chat_in_hash_dir(tmp_path):
    home = tmp_path / "gemini"
    digest = hashlib.sha256(CWD.encode()).hexdigest()
    chat = home / "tmp" / digest / "chats" / "session-old.json"
    chat.parent.mkdir(parents=True)
    lines = chat_lines("old")
    chat.write_text(json.dumps(dict(lines[0], projectHash=digest,
                                    messages=[l for l in lines[1:] if "type" in l])))
    (home / "projects.json").write_text(json.dumps({"projects": {CWD: "my-app"}}))
    provider = GeminiProvider(home)

    sources = provider.list_sources()

    assert [(s.external_id, s.project_key) for s in sources] == [("old", CWD)]
    assert provider.parse(sources[0]).first_prompt == "list the dependencies"


def test_missing_home_is_unavailable(tmp_path):
    provider = GeminiProvider(tmp_path / "nope")
    assert not provider.is_available()
    assert provider.list_sources() == []
