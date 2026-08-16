from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Favorite:
    """A folder the user has pinned in the sidebar for quick access."""

    id: Optional[int]
    path: str
    name: str
    created_at: Optional[str] = None


@dataclass
class FileEntry:
    """One row in the folder contents table - an immediate child (file or
    subfolder) of whatever folder is currently open.

    `size_bytes` is the file's own size for a file; for a directory it's
    always `None` for now - recursive folder size is a planned future
    feature (see FileSystemService), not computed here, so a folder's size
    column reads "-" rather than a misleadingly-cheap immediate-children-only
    number. `modified_at` is the epoch seconds from `os.stat().st_mtime`,
    rendered by formatting.format_modified - kept as a raw float here (not a
    pre-formatted string) so FolderTreeCtrl can sort on the real value
    rather than on formatted text.
    """

    name: str
    path: str
    is_dir: bool
    size_bytes: Optional[int] = None
    modified_at: Optional[float] = None


@dataclass
class FolderListing:
    """Result of FileSystemService.list_folder(path) - the immediate
    children of `path` plus whatever went wrong, if anything. `error` is set
    instead of `entries` when `path` itself couldn't be listed (removed,
    permission denied, no longer a directory, ...); `skipped` counts entries
    that could not be `os.stat`-ed individually (e.g. a broken symlink) and
    were left out of `entries` rather than aborting the whole listing."""

    entries: List[FileEntry] = field(default_factory=list)
    skipped: int = 0
    error: Optional[str] = None
