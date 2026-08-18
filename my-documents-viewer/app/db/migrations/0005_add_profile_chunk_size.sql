-- Per-profile chunk size (characters) used when splitting a document's text
-- into embeddable chunks (see app/chunking.py). Defaults to the app's
-- original fixed CHUNK_SIZE (800 chars) so existing profiles keep today's
-- behavior until a user opts into a different value via the profile dialog.
ALTER TABLE profiles ADD COLUMN chunk_size INTEGER NOT NULL DEFAULT 800;
