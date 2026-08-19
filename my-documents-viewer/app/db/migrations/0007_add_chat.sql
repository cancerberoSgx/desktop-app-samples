-- Chat-with-documents support. A profile can additionally choose a
-- text-generation ("chat") backend/model, alongside its existing embedding
-- config - see app/chat/ (ChatBackend, mirroring app/embeddings/
-- EmbeddingBackend) and app/chat_service.py. Reuses the profile's existing
-- openai_api_key/gemini_api_key columns (same providers as embeddings), so no
-- new key columns are needed. NULL chat_backend/chat_model means "chat not
-- configured for this profile yet" - see app/chat/__init__.py::get_chat_backend.
ALTER TABLE profiles ADD COLUMN chat_backend TEXT;
ALTER TABLE profiles ADD COLUMN chat_model TEXT;

-- One conversation is a named, ordered sequence of user/assistant turns,
-- scoped to a profile the same way documents are ("profiles scope
-- everything" - see CLAUDE.md). Deleting a profile cascades to its
-- conversations (and, via conversation_messages' own FK below, their
-- messages) the same way it already cascades to documents/chunks.
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversations_profile ON conversations(profile_id);

-- `references_json` (assistant messages only) is a JSON list of
-- SearchResult-shaped objects (chunk_id, document_id, document_path, snippet,
-- score, chunk_index, start_offset, end_offset, fts_rank, vector_rank,
-- vector_distance) - the same chunks hybrid_search() retrieved to answer this
-- turn, stored verbatim so a reloaded conversation can reopen
-- DocumentViewerFrame with the exact same highlighted span without
-- re-running search or guessing offsets. NULL/absent for user messages and
-- for an assistant turn that retrieved nothing.
CREATE TABLE conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    references_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversation_messages_conversation ON conversation_messages(conversation_id);
