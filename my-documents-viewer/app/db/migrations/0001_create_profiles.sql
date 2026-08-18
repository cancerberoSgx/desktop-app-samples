-- A profile is a document "kind" the user defines (e.g. "History",
-- "Development", "Contracts") - it scopes both which documents are indexed
-- together and which embedding model/backend is used to embed them.
CREATE TABLE profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    embedding_backend TEXT NOT NULL DEFAULT 'fastembed',
    embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-small-en-v1.5',
    embedding_dim INTEGER NOT NULL DEFAULT 384,
    openai_api_key TEXT DEFAULT NULL,
    gemini_api_key TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
