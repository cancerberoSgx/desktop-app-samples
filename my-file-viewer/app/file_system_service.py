import os
from typing import List

from .models import FileEntry, FolderListing

"""The "service" for every filesystem action this app performs, per
CLAUDE.md's async rule: every method here is a plain, blocking function -
none of them may touch wx or be called directly from an event handler.
Callers always go through AsyncTaskRunner (app/async_task.py), exactly the
way my-redis-viewer's DatasourceRepository.test_connection is only ever
invoked through AsyncTaskRunner from DatasourcesPage._on_connect.

Stateless by design (no sqlite connection, no in-memory cache) - unlike
FavoriteRepository/SettingsRepository, which own the sqlite3.Connection,
this class only ever talks to the OS filesystem. Today it has one action,
list_folder; every future folder action (rename, delete, copy/move, create
folder, recursive size, glob-based search, ...) belongs here as its own
method, called the same way: wrapped in a lambda passed to
AsyncTaskRunner.run(work=...), never called synchronously from a
wx.EVT_* handler. This is the pattern to keep reusing as the app grows.
"""


class FileSystemService:
    def list_folder(self, path: str) -> FolderListing:
        """List the immediate children of `path` - one os.stat per entry,
        cheap for realistic folder sizes but still routed through
        AsyncTaskRunner by every caller, both because a folder can be
        network-mounted (where even a single stat can stall) and to keep
        the "every filesystem action is async" rule exception-free as more,
        genuinely expensive actions (recursive size, glob search) join this
        class later.

        Returns FolderListing.error instead of raising when `path` itself
        can't be listed (removed, permission denied, no longer a
        directory). An entry that can't be individually stat-ed (e.g. a
        dangling symlink, or removed mid-scan) is left out of `entries` and
        counted in `skipped` rather than aborting the whole listing.
        """
        if not os.path.isdir(path):
            return FolderListing(error=f"'{path}' is not a folder.")

        entries: List[FileEntry] = []
        skipped = 0
        try:
            with os.scandir(path) as it:
                dir_entries = list(it)
        except OSError as exc:
            return FolderListing(error=str(exc))

        for dir_entry in dir_entries:
            try:
                stat = dir_entry.stat(follow_symlinks=False)
                is_dir = dir_entry.is_dir(follow_symlinks=False)
                entries.append(
                    FileEntry(
                        name=dir_entry.name,
                        path=dir_entry.path,
                        is_dir=is_dir,
                        size_bytes=None if is_dir else stat.st_size,
                        modified_at=stat.st_mtime,
                    )
                )
            except OSError:
                skipped += 1

        return FolderListing(entries=entries, skipped=skipped)
