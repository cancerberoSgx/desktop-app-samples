import os
import sqlite3
from typing import List, Optional, Set

from .models import Entry, ExtensionUsage, ScannedFile, SubtreeScan

"""SQLite read/write layer over the `folders`/`files` cache
(app/db/migrations/0001_create_scan_tables.sql) - the only module in this
app that knows their schema. `DiskScanRepository` never touches SQLite;
the UI (a later step) never writes SQL directly - every read it shows and
every write a scan produces goes through `CacheRepository` below.

Reload is what makes this a *cache* rather than a database of record: a
folder's row (and everything under it) is only ever produced by re-running
`DiskScanRepository` and replacing what was there - there is no
independent "edit this number" path. See `replace_subtree` for the delete-
then-insert semantics that keep it honest when files are removed from disk
between scans.
"""


class CacheRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_entry(self, path: str) -> Optional[Entry]:
        """The cached row for `path` itself, whichever table it's in -
        used to show the currently-open folder's own total/scanned-at.
        `None` means it has never been scanned (not "scanned as empty")."""
        row = self._conn.execute(
            "SELECT path, total_bytes, item_count, scanned_at, error FROM folders WHERE path = ?",
            (path,),
        ).fetchone()
        if row is not None:
            return _folder_row_to_entry(row)
        row = self._conn.execute(
            "SELECT path, size_bytes, scanned_at FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return _file_row_to_entry(row) if row is not None else None

    def list_children(self, folder_path: str) -> List[Entry]:
        """Every cached immediate child (subfolder + file) of
        `folder_path`, unsorted - the drill-down table (a later step)
        applies its own sort. Empty until Reload has run for this folder,
        or for an ancestor whose own Reload's `du` call already recursed
        through it (see module docstring on `app/disk_scan_repository.py`:
        one `scan_subdirectory` call covers its *entire* subtree, not just
        one level)."""
        entries = [
            _folder_row_to_entry(row)
            for row in self._conn.execute(
                "SELECT path, total_bytes, item_count, scanned_at, error FROM folders WHERE parent_path = ?",
                (folder_path,),
            )
        ]
        entries.extend(
            _file_row_to_entry(row)
            for row in self._conn.execute(
                "SELECT path, size_bytes, scanned_at FROM files WHERE parent_path = ?",
                (folder_path,),
            )
        )
        return entries

    def extension_breakdown(self, folder_path: str) -> List[ExtensionUsage]:
        """Every file recursively under `folder_path` (itself included),
        grouped by extension - backs the "by file type" chart (a later
        step). Uses GLOB rather than LIKE for the prefix match: a
        filesystem path can legally contain `%`/`_`, which LIKE treats as
        wildcards and would need escaping - GLOB's wildcards are `*`/`?`
        instead, so a real path's own characters are never
        misinterpreted. Both are indexed-prefix-scannable the same way in
        SQLite as long as the pattern has no leading wildcard, so this
        stays cheap even for a folder with a very large subtree."""
        pattern = _subtree_glob(folder_path)
        rows = self._conn.execute(
            "SELECT extension, SUM(size_bytes) AS total_bytes, COUNT(*) AS file_count "
            "FROM files WHERE path = ? OR path GLOB ? GROUP BY extension",
            (folder_path, pattern),
        ).fetchall()
        return [
            ExtensionUsage(
                extension=row["extension"],
                size_bytes=row["total_bytes"] or 0,
                file_count=row["file_count"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def prune_missing_children(self, folder_path: str, existing_paths: Set[str]) -> None:
        """Deletes cached folders/files rows directly inside `folder_path`
        whose path is no longer in `existing_paths` (a fresh
        `DiskScanRepository.list_immediate` result) - keeps the cache
        honest when something was deleted from disk since the last scan,
        without touching rows for paths still present (those get
        overwritten by `replace_files`/`replace_subtree` once their own
        fresh data lands, not deleted here first)."""
        cached_dirs = {
            row["path"] for row in self._conn.execute("SELECT path FROM folders WHERE parent_path = ?", (folder_path,))
        }
        cached_files = {
            row["path"] for row in self._conn.execute("SELECT path FROM files WHERE parent_path = ?", (folder_path,))
        }
        for path in cached_dirs - existing_paths:
            self._delete_subtree(path)
        stale_files = cached_files - existing_paths
        if stale_files:
            self._conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in stale_files])
        self._conn.commit()

    def replace_files(self, parent_path: str, files: List[ScannedFile]) -> None:
        """Full replace of every `files` row directly inside `parent_path` -
        safe in one shot because `DiskScanRepository.list_immediate`'s
        `os.stat` pass already has complete, correct info for every direct
        file, no further scanning needed for these rows."""
        self._conn.execute("DELETE FROM files WHERE parent_path = ?", (parent_path,))
        self._conn.executemany(
            "INSERT INTO files (path, parent_path, size_bytes, extension) VALUES (?, ?, ?, ?)",
            [(f.path, f.parent_path, f.size_bytes, f.extension) for f in files],
        )
        self._conn.commit()

    def replace_subtree(self, scan: SubtreeScan, error: Optional[str] = None) -> None:
        """Replaces every folders/files row at-or-under `scan.root_path`
        with the fresh result of recursively scanning that one
        subdirectory (`DiskScanRepository.scan_subdirectory`) - deletes the
        old subtree first so a file/folder removed from disk since the
        last scan doesn't linger in the cache. `error` is attached to
        `scan.root_path`'s own row (e.g. "some descendant was
        inaccessible") without discarding whatever totals `du` still
        managed to compute for the rest of the subtree."""
        self._delete_subtree(scan.root_path)
        self._conn.executemany(
            "INSERT INTO folders (path, parent_path, total_bytes, item_count, error) VALUES (?, ?, ?, ?, ?)",
            [
                (d.path, d.parent_path, d.size_bytes, d.item_count, error if d.path == scan.root_path else None)
                for d in scan.dirs
            ],
        )
        self._conn.executemany(
            "INSERT INTO files (path, parent_path, size_bytes, extension) VALUES (?, ?, ?, ?)",
            [(f.path, f.parent_path, f.size_bytes, f.extension) for f in scan.files],
        )
        self._conn.commit()

    def upsert_folder_summary(
        self,
        path: str,
        parent_path: Optional[str],
        total_bytes: Optional[int],
        item_count: Optional[int],
        error: Optional[str] = None,
    ) -> None:
        """Writes/overwrites just one folder's own row - used for the
        top-level folder currently open in the explorer UI, whose total
        isn't itself a `du` target but the sum of its already-scanned
        immediate children (computed by the caller once every child job
        finishes). `replace_subtree` covers every OTHER directory `du`
        finds within a scanned subtree; this is the one row a scan can't
        produce on its own, because the folder the user opened is `du`'s
        *caller*, never its argument."""
        self._conn.execute(
            "INSERT INTO folders (path, parent_path, total_bytes, item_count, error) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "parent_path = excluded.parent_path, total_bytes = excluded.total_bytes, "
            "item_count = excluded.item_count, scanned_at = datetime('now'), error = excluded.error",
            (path, parent_path, total_bytes, item_count, error),
        )
        self._conn.commit()

    def _delete_subtree(self, path: str) -> None:
        pattern = _subtree_glob(path)
        self._conn.execute("DELETE FROM folders WHERE path = ? OR path GLOB ?", (path, pattern))
        self._conn.execute("DELETE FROM files WHERE path = ? OR path GLOB ?", (path, pattern))


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite) - same shape and
    purpose as my-docker-viewer's: reserved for the recent-folders list a
    later step's "Open Folder" toolbar will offer, not read/written by
    anything yet."""

    def __init__(self, conn: sqlite3.Connection) -> None:
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


def _subtree_glob(path: str) -> str:
    return path.rstrip("/") + "/*"


def _folder_row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        path=row["path"],
        name=_basename(row["path"]),
        is_dir=True,
        size_bytes=row["total_bytes"],
        item_count=row["item_count"],
        scanned_at=row["scanned_at"],
        error=row["error"],
    )


def _file_row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        path=row["path"],
        name=_basename(row["path"]),
        is_dir=False,
        size_bytes=row["size_bytes"],
        item_count=1,
        scanned_at=row["scanned_at"],
        error=None,
    )


def _basename(path: str) -> str:
    return os.path.basename(path) or path
