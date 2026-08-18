-- One row per indexed source file. content_hash lets re-indexing skip a
-- file that hasn't changed since its last index; embedding_backend/model are
-- stamped on the row (rather than only trusted from the parent profile) so a
-- later profile embedding-model change can be detected per document too.
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT,
    embedding_backend TEXT,
    embedding_model TEXT,
    UNIQUE(profile_id, path)
);

CREATE INDEX idx_documents_profile ON documents(profile_id);
