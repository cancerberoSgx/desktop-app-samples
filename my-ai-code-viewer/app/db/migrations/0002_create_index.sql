-- Local index/archive of every conversation the providers (app/providers/)
-- have ever reported. Rows are never deleted just because the source went
-- away (Claude Code prunes transcripts after ~30 days) - instead
-- conversations.missing_from_source flips to 1, and the archived messages
-- below keep the conversation readable.

CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,          -- 'claude-code' | 'opencode' | ...
    project_key TEXT NOT NULL,       -- provider-specific stable id (e.g. Claude's encoded folder name)
    path TEXT NOT NULL,              -- best-known real filesystem path of the project
    name TEXT NOT NULL,              -- display name (basename of path)
    last_activity TEXT,              -- ISO-8601 UTC of newest conversation update
    UNIQUE (provider, project_key)
);

CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,       -- session id in the provider (Claude uuid, OpenCode ses_...)
    parent_external_id TEXT,         -- set for subagent threads: the parent session's external_id
    title TEXT,
    first_prompt TEXT,
    model TEXT,
    cwd TEXT,
    git_branch TEXT,
    started_at TEXT,
    updated_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    user_message_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    tools_used TEXT,                 -- JSON object {tool name: call count}
    source_path TEXT,
    source_fingerprint TEXT,         -- change detector (mtime/size, or provider's own updated stamp)
    missing_from_source INTEGER NOT NULL DEFAULT 0,
    UNIQUE (provider, external_id)
);
CREATE INDEX conversations_project_idx ON conversations (project_id);

-- One row per content block (text / thinking / tool_use / tool_result /
-- error), in display order. Kept flat and text-only on purpose so a future
-- cross-project full-text search can be added as an FTS5 table over
-- messages.text without reshaping anything.
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,              -- 'user' | 'assistant' | 'system'
    kind TEXT NOT NULL,              -- 'text' | 'thinking' | 'tool_use' | 'tool_result' | 'error'
    tool_name TEXT,
    text TEXT NOT NULL DEFAULT '',
    timestamp TEXT,
    is_error INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, seq);
