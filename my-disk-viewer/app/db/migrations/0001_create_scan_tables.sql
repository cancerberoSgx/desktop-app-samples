-- One row per directory ever scanned. `total_bytes`/`item_count` are the
-- RECURSIVE total for everything under this path (every file anywhere in
-- its subtree), not just its immediate children - that's the number the
-- table/chart views need to answer "which folder is biggest". NULL until
-- a scan has produced it (see CacheRepository.upsert_folder_summary /
-- replace_subtree in app/cache_repository.py).
CREATE TABLE folders (
    path TEXT PRIMARY KEY,
    parent_path TEXT,
    total_bytes INTEGER,
    item_count INTEGER,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    error TEXT
);
CREATE INDEX idx_folders_parent ON folders(parent_path);

-- One row per file ever scanned. `size_bytes` is real allocated disk
-- usage (du's/os.stat's block count, not a file's logical/apparent size -
-- see app/disk_scan_repository.py). `extension` is lowercased with the
-- leading dot stripped ('' for an extensionless file), indexed on its own
-- for the "by file type" chart.
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    parent_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    extension TEXT NOT NULL DEFAULT '',
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_files_parent ON files(parent_path);
CREATE INDEX idx_files_extension ON files(extension);
