from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
    rendered by formatting.format_timestamp - kept as a raw float here (not a
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


@dataclass
class DeleteResult:
    """Result of FileSystemService.delete(paths) - a batch action, so unlike
    list_folder's single `error`, each path either succeeds (`deleted`) or
    fails independently (`errors`, path -> message) rather than the whole
    call aborting on the first failure."""

    deleted: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class FileProperties:
    """Result of FileSystemService.get_properties(path) - backs the
    Properties dialog. A single `os.stat()` call's worth of data, so always
    fast regardless of whether `path` is a file or a folder - deliberately
    does NOT include a folder's recursive size (see
    FileSystemService.calculate_folder_size, called separately/async) since
    walking a whole tree can be arbitrarily slow, unlike everything else
    here. `size_bytes` is therefore only meaningful when `is_dir` is
    `False`; PropertiesDialog is the one place that knows to kick off the
    separate recursive-size fetch instead of reading this field when
    `is_dir` is `True`."""

    name: str
    extension: str
    path: str
    is_dir: bool
    size_bytes: Optional[int]
    permissions: str
    created_at: Optional[float]
    modified_at: Optional[float]
    accessed_at: Optional[float]
