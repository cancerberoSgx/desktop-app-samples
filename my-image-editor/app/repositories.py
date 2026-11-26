import sqlite3
from typing import Optional

# Preferences (File > Preferences)
JPEG_QUALITY_SETTING_KEY = "jpeg_quality"
PNG_COMPRESS_LEVEL_SETTING_KEY = "png_compress_level"
OPEN_ZOOM_SETTING_KEY = "open_zoom"          # "fit" | "actual"

# "Restore where I left off" bits
LAST_DIR_SETTING_KEY = "last_dir"
SIDEBAR_COLLAPSED_SETTING_KEY = "sidebar_collapsed"
DEFAULT_STYLE_SETTING_KEY = "default_style"  # JSON, see editor.shapes.Style.to_dict

DEFAULT_JPEG_QUALITY = 90
DEFAULT_PNG_COMPRESS_LEVEL = 6
OPEN_ZOOM_FIT, OPEN_ZOOM_ACTUAL = "fit", "actual"
DEFAULT_OPEN_ZOOM = OPEN_ZOOM_FIT


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite) - backs both the
    Preferences dialog and the "restore where I left off" bits (window
    geometry, sidebar state, last folder, the style new shapes get)."""

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

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        return default if value is None else value == "1"

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, "1" if value else "0")

    def get_int(self, key: str) -> Optional[int]:
        value = self.get(key)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    def set_int(self, key: str, value: Optional[int]) -> None:
        self.set(key, str(value) if value is not None else None)

    def get_int_in(self, key: str, default: int, low: int, high: int) -> int:
        """An int setting, falling back to `default` when unset or out of range."""
        value = self.get_int(key)
        return value if value is not None and low <= value <= high else default
