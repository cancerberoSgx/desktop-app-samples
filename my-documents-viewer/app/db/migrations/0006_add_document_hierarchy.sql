-- Structured data import support: a CSV/JSON file can be imported as a
-- "container" document (no content/chunks of its own) with many "record"
-- child documents underneath it - one per row/object - via
-- parent_document_id. See app/data_import.py and
-- DocumentRepository.import_data_file. `kind` distinguishes the three
-- document shapes now possible; existing rows default to 'file' (today's
-- one-document-per-source-file case), so no backfill is needed.
--
-- `row_key` is a stable per-row identity (the value of a user-chosen id
-- column, or a content hash if none was chosen - see
-- data_import.resolve_row_key) used to match a record back to its source
-- row across re-imports. Identity is looked up through the unique index
-- below, scoped by container - never by parsing `path` apart. `path` stays
-- NOT NULL/globally unique for records too (DocumentRepository generates
-- one by appending the record's own id, guaranteeing uniqueness by
-- construction), but it's a display/storage artifact only.
--
-- `properties_json` holds, for a container, its import config (format,
-- column mapping, row count); for a record, the raw original field values.
-- Both are display-only, never queried by search.
ALTER TABLE documents ADD COLUMN parent_document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE;
ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'file';
ALTER TABLE documents ADD COLUMN row_key TEXT;
ALTER TABLE documents ADD COLUMN properties_json TEXT;

CREATE INDEX idx_documents_parent ON documents(parent_document_id);

-- Only meaningful for records (parent_document_id NOT NULL) - containers and
-- plain files never set row_key, so this partial index doesn't apply to them.
CREATE UNIQUE INDEX idx_documents_row_key ON documents(profile_id, parent_document_id, row_key)
    WHERE parent_document_id IS NOT NULL;
