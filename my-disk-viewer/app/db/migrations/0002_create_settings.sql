-- Plain key/value app settings - same shape as my-docker-viewer's. Not
-- read/written by any screen yet; reserved for the recent-folders list the
-- explorer UI (a later step) will offer in its "Open Folder" toolbar.
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
