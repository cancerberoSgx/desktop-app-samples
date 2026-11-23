import sqlite3
from pathlib import Path
from typing import Optional

from .paths import db_path


def get_connection(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the app's own SQLite database.

    WAL + a busy timeout because the background sync (app/indexer.py) writes
    through its *own* connection on a worker thread while the UI thread keeps
    reading through the main one - WAL lets readers proceed during a write,
    and the timeout covers the brief moments two writers overlap (e.g. a
    settings save while a sync commits).
    """
    conn = sqlite3.connect(path or db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
