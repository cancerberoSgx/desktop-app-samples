import sqlite3
from pathlib import Path
from typing import Optional

from .paths import db_path


def get_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the app's own SQLite database (~/.my-image-editor/my-image-editor.db).

    Only preferences live here for now; new features add tables through a new
    numbered file in app/db/migrations. WAL + a busy timeout keep it safe to
    add a second connection later (e.g. a worker thread), like the sibling
    apps do.
    """
    conn = sqlite3.connect(path or db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
