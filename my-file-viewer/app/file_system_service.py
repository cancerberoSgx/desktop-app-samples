import os
import shutil
import stat as stat_module
from typing import List

from .models import DeleteResult, FileEntry, FolderListing

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
    def list_folder(self, path: str, show_hidden: bool = False) -> FolderListing:
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

        `show_hidden` (default `False`, matching the app's default) - when
        `False`, an entry that's hidden (Unix dotfile convention, or the
        Windows FILE_ATTRIBUTE_HIDDEN attribute) is silently left out of
        `entries` too, but *not* counted in `skipped`: that count means
        "couldn't be read", not "deliberately filtered out". The caller
        (FolderExplorerPage) is the one that knows the user's current
        preference and passes it through on every call, including lazy
        per-row expands - see FolderExplorerPage._show_hidden.
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
                if not show_hidden and _is_hidden(dir_entry.name, stat):
                    continue
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

    def delete(self, paths: List[str]) -> DeleteResult:
        """Deletes every path in `paths` - a file via os.remove, a folder
        (recursively) via shutil.rmtree. Each path succeeds or fails on its
        own (see DeleteResult) rather than the whole batch aborting on the
        first failure, since the caller (FolderExplorerPage.delete_selected)
        may be deleting a mixed multi-selection where one item being
        read-only/gone-by-the-time-we-get-to-it shouldn't stop the rest."""
        result = DeleteResult()
        for path in paths:
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                result.deleted.append(path)
            except OSError as exc:
                result.errors[path] = str(exc)
        return result

    def rename(self, path: str, new_name: str) -> str:
        """Renames the file/folder at `path` to `new_name` (a bare name,
        not a path - renaming can't move an entry to a different folder)
        and returns the new absolute path. Raises ValueError for a name
        that's empty or contains a path separator, and whatever OSError
        os.rename itself raises (e.g. FileExistsError, PermissionError) -
        both are surfaced identically by the caller via AsyncTaskRunner's
        on_error, so no special-casing is needed there."""
        new_name = new_name.strip()
        if not new_name or os.sep in new_name or (os.altsep and os.altsep in new_name):
            raise ValueError("Enter a valid name.")
        new_path = os.path.join(os.path.dirname(path), new_name)
        os.rename(path, new_path)
        return new_path


def _is_hidden(name: str, stat_result: os.stat_result) -> bool:
    """Unix convention (dotfile) or Windows' FILE_ATTRIBUTE_HIDDEN - checked
    unconditionally rather than gated on sys.platform, since `st_file_attributes`
    simply doesn't exist on stat results outside Windows (getattr's default
    covers that) and a literal leading dot is a meaningless-but-harmless
    check to make on Windows."""
    if name.startswith("."):
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & stat_module.FILE_ATTRIBUTE_HIDDEN)
