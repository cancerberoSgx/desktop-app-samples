import sqlite3
from pathlib import Path


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    """Apply any *.sql file in `migrations_dir` not yet recorded as applied.

    Files are applied in filename order, so migrations are named with a
    numeric prefix (e.g. 0001_create_favorites.sql) to control ordering.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
