-- Documents are split into overlapping text chunks (see app/chunking.py) -
-- both the FTS5 keyword index and the per-profile sqlite-vec vector index
-- (see repositories.DocumentRepository._ensure_vec_table - it can't be a
-- static migration because its column dimension depends on the profile's
-- chosen embedding model) key off this table's `id` as their rowid, so a
-- chunk row is the shared join point between "found by keyword" and "found
-- by similarity" for hybrid search / reciprocal rank fusion.
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL
);

CREATE INDEX idx_chunks_document ON chunks(document_id);
CREATE INDEX idx_chunks_profile ON chunks(profile_id);

-- External-content FTS5 table: it stores no text of its own, just the
-- index, and is kept in sync with `chunks` via the triggers below (the
-- standard sqlite FTS5 external-content pattern) - this avoids storing
-- every chunk's text twice.
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
END;

CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
