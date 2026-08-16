import os
import sqlite3
from typing import List, Optional

from .models import Favorite

LAST_FOLDER_SETTING_KEY = "last_folder_path"
SIDEBAR_COLLAPSED_SETTING_KEY = "sidebar_collapsed"
SHOW_HIDDEN_FILES_SETTING_KEY = "show_hidden_files"


class FavoriteRepository:
    """CRUD for `favorites` (pure SQL against SQLite) - the folders pinned
    in the sidebar. `path` is stored normalized (os.path.abspath) and is
    UNIQUE, so adding the same folder twice is a no-op rather than a
    duplicate row (see add_folder)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def list(self) -> List[Favorite]:
        rows = self._conn.execute("SELECT * FROM favorites ORDER BY name").fetchall()
        return [self._row_to_favorite(row) for row in rows]

    def get_by_path(self, path: str) -> Optional[Favorite]:
        row = self._conn.execute(
            "SELECT * FROM favorites WHERE path = ?", (os.path.abspath(path),)
        ).fetchone()
        return self._row_to_favorite(row) if row else None

    def add_folder(self, path: str) -> Favorite:
        """Add `path` as a favorite, named after its folder basename.
        Idempotent: adding an already-favorited path just returns the
        existing row instead of raising the UNIQUE constraint violation."""
        path = os.path.abspath(path)
        existing = self.get_by_path(path)
        if existing is not None:
            return existing
        name = os.path.basename(path.rstrip(os.sep)) or path
        cursor = self._conn.execute(
            "INSERT INTO favorites (path, name) VALUES (?, ?)", (path, name)
        )
        self._conn.commit()
        return self.get(cursor.lastrowid)

    def get(self, favorite_id: int) -> Optional[Favorite]:
        row = self._conn.execute(
            "SELECT * FROM favorites WHERE id = ?", (favorite_id,)
        ).fetchone()
        return self._row_to_favorite(row) if row else None

    def remove(self, favorite_id: int) -> None:
        self._conn.execute("DELETE FROM favorites WHERE id = ?", (favorite_id,))
        self._conn.commit()

    def remove_by_path(self, path: str) -> None:
        self._conn.execute("DELETE FROM favorites WHERE path = ?", (os.path.abspath(path),))
        self._conn.commit()

    @staticmethod
    def _row_to_favorite(row: sqlite3.Row) -> Favorite:
        return Favorite(
            id=row["id"],
            path=row["path"],
            name=row["name"],
            created_at=row["created_at"],
        )


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite), used to remember
    which folder was last open and whether the sidebar was collapsed, so
    both can be restored on startup."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: Optional[str]) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_last_folder_path(self) -> Optional[str]:
        return self.get(LAST_FOLDER_SETTING_KEY)

    def set_last_folder_path(self, path: Optional[str]) -> None:
        self.set(LAST_FOLDER_SETTING_KEY, path)

    def get_sidebar_collapsed(self) -> bool:
        return self.get(SIDEBAR_COLLAPSED_SETTING_KEY) == "1"

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.set(SIDEBAR_COLLAPSED_SETTING_KEY, "1" if collapsed else "0")

    def get_show_hidden_files(self) -> bool:
        """Defaults to `False` (hidden files/folders not shown) when never
        set - same "no row yet -> falsy" convention as
        get_sidebar_collapsed."""
        return self.get(SHOW_HIDDEN_FILES_SETTING_KEY) == "1"

    def set_show_hidden_files(self, show_hidden: bool) -> None:
        self.set(SHOW_HIDDEN_FILES_SETTING_KEY, "1" if show_hidden else "0")
