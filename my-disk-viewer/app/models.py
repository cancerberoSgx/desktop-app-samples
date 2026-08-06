from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Entry:
    """One row as shown in the drill-down table: an immediate child (file
    or folder) of whatever folder is currently open, read straight off the
    SQLite cache (`CacheRepository`) - never computed directly from a `du`
    call itself, see `DiskScanRepository` for that side.

    For a directory, `size_bytes`/`item_count` are the *recursive* total of
    everything under it - every file anywhere in its subtree, not just its
    immediate children - since that's the number that answers "which
    subfolder is biggest". For a file they're just that file's own size / 1.
    Both are `None` until a scan has produced them, which the UI (a later
    step) renders the same way `ContainerDiskUsage`/`Volume` in
    my-docker-viewer render an unsized row: "Not scanned" rather than 0.

    `error` carries why a directory's total could not be fully computed
    (e.g. some descendant was unreadable) without blocking the rest of the
    listing - same posture as `ContainerDiskUsage.error` there: one bad
    subtree doesn't blank out an otherwise-good number."""

    path: str
    name: str
    is_dir: bool
    size_bytes: Optional[int] = None
    item_count: Optional[int] = None
    scanned_at: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_scanned(self) -> bool:
        return self.size_bytes is not None or self.error is not None


@dataclass
class ExtensionUsage:
    """One slice of the "by file type" chart: total bytes and file count
    across every file with this extension, recursively under whatever
    folder is currently selected (`CacheRepository.extension_breakdown`).
    `extension` is lowercased with the leading dot stripped; `""` groups
    every extensionless file (Makefile, LICENSE, ...) together."""

    extension: str
    size_bytes: int
    file_count: int


@dataclass
class ScannedFile:
    """One file as freshly measured - either by `DiskScanRepository.
    list_immediate`'s direct `os.stat` pass, or parsed out of a
    `scan_subdirectory` `du` run. Intermediate data on its way into the
    cache (`CacheRepository.replace_files`/`replace_subtree`), not what the
    UI reads back out - that's `Entry`."""

    path: str
    parent_path: str
    size_bytes: int
    extension: str


@dataclass
class ScannedDir:
    """One directory as freshly measured by `DiskScanRepository.
    scan_subdirectory` - `size_bytes`/`item_count` are already the
    recursive totals for everything under `path` (that's what a `du -a`
    line for a directory means), for `path` itself and for every
    descendant directory `du` walked through in the same run. Intermediate
    data on its way into the cache via `CacheRepository.replace_subtree`."""

    path: str
    parent_path: str
    size_bytes: int
    item_count: int


@dataclass
class SubtreeScan:
    """Everything `DiskScanRepository.scan_subdirectory(root_path)`
    produced from one `du -a -k -x` run: `root_path` itself plus every file
    and directory anywhere underneath it, since that single command
    recurses through the whole subtree - not just root_path's immediate
    children. `warnings` is `du`'s own stderr when it hit something it
    couldn't fully read (permission denied on a descendant, typically) but
    still produced usable results for the rest - see
    `disk_scan_repository._run_du`."""

    root_path: str
    files: List[ScannedFile] = field(default_factory=list)
    dirs: List[ScannedDir] = field(default_factory=list)
    warnings: Optional[str] = None
    # Symlinks `du -a` still lists (it doesn't follow them, but `-a` means
    # "list every entry", so it prints the link itself as its own tiny
    # entry) - excluded from `files`/`dirs` and counted here instead, same
    # "skip symlinks, don't size them" rule `list_immediate` applies to a
    # folder's direct children. See `_build_subtree_scan`.
    skipped: int = 0


@dataclass
class ImmediateListing:
    """Result of `DiskScanRepository.list_immediate` - `folder_path`'s own
    direct children only, cheap and stat-based, no `du` call (mirrors
    `DiskUsageRepository.list_targets`'s "loads automatically, no Calculate
    needed" cheap half in my-docker-viewer). Direct files are fully known
    from this alone (one `os.stat` each, already `ScannedFile`); direct
    subdirectories are returned as bare paths only - their totals only
    come from their own `scan_subdirectory` call.

    `skipped` counts symlinks and other-filesystem mount points excluded
    per this app's scan-scope defaults (stay on one filesystem, don't
    follow symlinks - same as `scan_subdirectory`'s `-x`, kept consistent
    so a folder's total doesn't depend on which half computed it) - not an
    error, just a transparency count the UI can show alongside the total.
    `error` is set instead of everything else when `folder_path` itself
    couldn't even be opened (removed, permission denied, ...)."""

    subdirs: List[str] = field(default_factory=list)
    files: List[ScannedFile] = field(default_factory=list)
    skipped: int = 0
    error: Optional[str] = None
