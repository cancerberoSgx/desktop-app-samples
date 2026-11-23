"""Shared fixtures. Everything is real (in-memory/temp SQLite, real files
written to tmp_path) - nothing is mocked, and nothing touches the user's
actual ~/.claude or OpenCode data."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.db.migrator import run_migrations  # noqa: E402
from app.db.paths import migrations_dir  # noqa: E402
from app.repositories import IndexRepository, SettingsRepository  # noqa: E402


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    run_migrations(connection, migrations_dir())
    yield connection
    connection.close()


@pytest.fixture
def settings_repo(conn) -> SettingsRepository:
    return SettingsRepository(conn)


@pytest.fixture
def index_repo(conn) -> IndexRepository:
    return IndexRepository(conn)


def write_jsonl(path: Path, lines) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


CWD = "/home/me/my-app.v2"
PROJECT_KEY = "-home-me-my-app-v2"


def claude_session_lines(session_id: str = "s1", cwd: str = CWD):
    return [
        {"type": "mode", "mode": "normal", "sessionId": session_id},
        {"type": "user", "uuid": "u1", "parentUuid": None, "sessionId": session_id, "cwd": cwd,
         "gitBranch": "main", "timestamp": "2026-01-01T10:00:00.000Z",
         "message": {"role": "user", "content": "list the files"}},
        {"type": "user", "uuid": "u-meta", "isMeta": True, "cwd": cwd, "timestamp": "2026-01-01T10:00:00.500Z",
         "message": {"role": "user", "content": "injected skill text"}},
        {"type": "assistant", "uuid": "a1", "cwd": cwd, "timestamp": "2026-01-01T10:00:01.000Z",
         "message": {"id": "msg_1", "role": "assistant", "model": "claude-opus-5-5",
                     "content": [{"type": "thinking", "thinking": "Let me look."}],
                     "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                               "cache_creation_input_tokens": 50}}},
        {"type": "assistant", "uuid": "a2", "cwd": cwd, "timestamp": "2026-01-01T10:00:02.000Z",
         "message": {"id": "msg_1", "role": "assistant", "model": "claude-opus-5-5",
                     "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                  "input": {"command": "ls -la"}}],
                     "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 100,
                               "cache_creation_input_tokens": 50}}},
        {"type": "user", "uuid": "u2", "cwd": cwd, "timestamp": "2026-01-01T10:00:03.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.txt\nb.txt", "is_error": False}]}},
        {"type": "assistant", "uuid": "a3", "cwd": cwd, "timestamp": "2026-01-01T10:00:04.000Z",
         "message": {"id": "msg_2", "role": "assistant", "model": "claude-opus-5-5",
                     "content": [{"type": "text", "text": "Two files: a.txt and b.txt"}],
                     "usage": {"input_tokens": 1, "output_tokens": 7}}},
        # duplicated line (resumed sessions replay history) must be ignored
        {"type": "assistant", "uuid": "a3", "cwd": cwd, "timestamp": "2026-01-01T10:00:04.000Z",
         "message": {"id": "msg_2", "role": "assistant", "content": [{"type": "text", "text": "dup"}]}},
        {"type": "ai-title", "aiTitle": "List files", "sessionId": session_id},
        {"type": "cost-state", "totalCostUSD": 0.42, "sessionId": session_id},
        "not-a-dict-line",
    ]


@pytest.fixture
def claude_root(tmp_path: Path) -> Path:
    root = tmp_path / "claude-projects"
    write_jsonl(root / PROJECT_KEY / "s1.jsonl", claude_session_lines())
    sub = write_jsonl(root / PROJECT_KEY / "s1" / "subagents" / "agent-abc.jsonl", [
        {"type": "user", "uuid": "su1", "isSidechain": True, "cwd": CWD, "timestamp": "2026-01-01T10:00:05.000Z",
         "message": {"role": "user", "content": "review the code"}},
        {"type": "assistant", "uuid": "sa1", "isSidechain": True, "cwd": CWD,
         "timestamp": "2026-01-01T10:00:06.000Z",
         "message": {"id": "msg_s", "role": "assistant", "content": [{"type": "text", "text": "Looks fine."}]}},
    ])
    (sub.parent / "agent-abc.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "description": "Review code"}), encoding="utf-8")
    (root / "-home-me-memory-only" / "memory").mkdir(parents=True)
    return root


OPENCODE_SCHEMA = """
CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT, directory TEXT NOT NULL,
  title TEXT NOT NULL, model TEXT, cost REAL DEFAULT 0 NOT NULL, tokens_input INTEGER DEFAULT 0 NOT NULL,
  tokens_output INTEGER DEFAULT 0 NOT NULL, tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
  tokens_cache_read INTEGER DEFAULT 0 NOT NULL, tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
  time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
  time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL);
"""


@pytest.fixture
def opencode_db(tmp_path: Path) -> Path:
    path = tmp_path / "opencode.db"
    db = sqlite3.connect(path)
    db.executescript(OPENCODE_SCHEMA)
    db.execute("INSERT INTO session VALUES ('ses_1','p1',NULL,?,'New session - 2026-01-01',"
               "'{\"id\":\"deepseek\"}',0.1,100,20,5,1000,0,1767261600000,1767261660000)", (CWD,))
    db.execute("INSERT INTO message VALUES ('msg_a','ses_1',1767261600000,1767261600000,?)",
               (json.dumps({"role": "user"}),))
    db.execute("INSERT INTO message VALUES ('msg_b','ses_1',1767261601000,1767261601000,?)",
               (json.dumps({"role": "assistant", "modelID": "deepseek-v4"}),))
    db.execute("INSERT INTO message VALUES ('msg_c','ses_1',1767261602000,1767261602000,?)",
               (json.dumps({"role": "assistant", "error": {"name": "APIError",
                                                            "data": {"message": "Insufficient Balance"}}}),))
    parts = [
        ("prt_1", "msg_a", {"type": "text", "text": "build me an app"}),
        ("prt_2", "msg_b", {"type": "step-start"}),
        ("prt_3", "msg_b", {"type": "reasoning", "text": "thinking hard"}),
        ("prt_4", "msg_b", {"type": "tool", "tool": "bash",
                            "state": {"status": "completed", "input": {"command": "ls"}, "output": "x.py"}}),
        ("prt_5", "msg_b", {"type": "tool", "tool": "read",
                            "state": {"status": "error", "input": {"filePath": "/nope"}, "error": "not found"}}),
        ("prt_6", "msg_b", {"type": "text", "text": "Done."}),
    ]
    for pid, mid, data in parts:
        db.execute("INSERT INTO part VALUES (?,?,'ses_1',0,0,?)", (pid, mid, json.dumps(data)))
    db.commit()
    db.close()
    return path
