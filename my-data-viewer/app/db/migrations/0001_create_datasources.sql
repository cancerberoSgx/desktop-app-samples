CREATE TABLE datasources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    file_path TEXT DEFAULT NULL,
    db_host TEXT DEFAULT NULL,
    db_port INTEGER DEFAULT NULL,
    db_name TEXT DEFAULT NULL,
    db_user TEXT DEFAULT NULL,
    db_password TEXT DEFAULT NULL
);
