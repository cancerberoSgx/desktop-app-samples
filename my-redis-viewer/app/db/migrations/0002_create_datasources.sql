CREATE TABLE datasources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
    redis_host TEXT NOT NULL DEFAULT 'localhost',
    redis_port INTEGER NOT NULL DEFAULT 6379,
    redis_user TEXT DEFAULT NULL,
    redis_password TEXT DEFAULT NULL
);
