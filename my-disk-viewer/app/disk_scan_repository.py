import os
import subprocess
from typing import Dict, List, Optional, Tuple

from .models import ImmediateListing, ScannedDir, ScannedFile, SubtreeScan

"""Wraps the `du` CLI - no docker-viewer-style SDK dependency here either,
and no OS-specific branch anywhere in this module: `-a -k -x` behaves
identically on GNU du (Linux) and BSD du (macOS), so the same argv and the
same parser serve both.

`du` was chosen over the alternatives after comparing:
- ncdu / dua / dust / gdu - faster or friendlier on huge trees, but none of
  them ships by default on Linux or macOS, unlike `du` - using them would
  mean bundling a per-platform binary, the same tradeoff my-docker-viewer
  deliberately avoided by shelling out to `docker` instead of vendoring a
  Docker SDK.
- A pure-Python `os.scandir`/`os.stat` recursive walker - fully portable
  (including to a future Windows backend, where there's no `du` at all)
  and gives full control over incremental progress, but reinvents what a
  C-optimized `du` already does well, and is measurably slower on large
  trees. `list_immediate` below still uses this approach, but only for one
  folder's *immediate* children (cheap, no recursion) - the expensive
  recursive walk stays delegated to `du`.

`du -k` reports whole KiB blocks of *allocated disk usage*, not a file's
apparent/logical size (`st_size`) - deliberately: that's the number that
answers "what's actually eating my disk" (matters for sparse files,
filesystem block rounding, hardlinks), and it's literally what `du` is
named for.
"""

DU_BINARY = "du"
# A full subtree walk can legitimately take minutes on a large folder, so
# this is a safety net against a genuinely hung process (e.g. a network
# mount that's the *same* filesystem `-x` wouldn't exclude, just
# unresponsive) - not a "should normally finish well before this" budget.
DEFAULT_SCAN_TIMEOUT_SECONDS = 900


class DuNotAvailableError(RuntimeError):
    """Raised when the `du` executable itself cannot be found on PATH."""


class DuCommandError(RuntimeError):
    """Raised when `du` fails outright - the target path doesn't exist (or
    stopped existing mid-scan) or is entirely unreadable, i.e. it produced
    no usable output at all. A `du` run that *partially* succeeds (some
    descendant inaccessible, most of the tree fine) is NOT this - see
    `_run_du`'s return value, which surfaces that case as a warnings string
    alongside the still-useful stdout instead of raising."""


def _run_du(args: List[str], timeout: int) -> Tuple[str, Optional[str]]:
    """Shells out to `du`, returning (stdout, warnings).

    `du` exits non-zero whenever any part of the walk hit a problem (most
    commonly "Permission denied" on one descendant it couldn't read) but
    still prints correct, usable results for everything it *could* read -
    unlike a command that either fully succeeds or fully fails, a non-zero
    exit here does NOT mean "discard the output". Only an *empty* stdout is
    treated as a hard failure (the target itself was missing or entirely
    unreadable); otherwise stderr is returned as `warnings` for the caller
    to attach to that subtree's result without losing the rest of it - the
    same "one bad part doesn't blank out an otherwise-good number" posture
    as `DiskUsageRepository.sum_mounts_bytes` in my-docker-viewer.
    """
    try:
        result = subprocess.run(
            [DU_BINARY, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DuNotAvailableError(
            "The 'du' command was not found on PATH. It ships with every "
            "Linux and macOS system, so this usually means PATH itself is "
            "misconfigured for this app."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DuCommandError(f"du {' '.join(args)} timed out after {timeout}s.") from exc

    if not result.stdout.strip():
        raise DuCommandError(
            result.stderr.strip() or f"du {' '.join(args)} produced no output (exit code {result.returncode})."
        )
    warnings = result.stderr.strip() or None
    return result.stdout, warnings


def _parse_du_lines(text: str) -> List[Tuple[str, int]]:
    """Parses `du -a -k` output - "<size_in_kib>\\t<path>" per line, the one
    format GNU and BSD `du` agree on - into (path, size_bytes) pairs.
    `-k` reports whole KiB blocks (rounded up), multiplied back out to
    bytes here."""
    pairs = []
    for line in text.splitlines():
        if not line:
            continue
        size_kib_str, _, path = line.partition("\t")
        pairs.append((path, int(size_kib_str) * 1024))
    return pairs


def _build_subtree_scan(root_path: str, pairs: List[Tuple[str, int]], warnings: Optional[str]) -> SubtreeScan:
    """Turns raw (path, size_bytes) pairs from one `du -a -k -x` run into
    the ScannedFile/ScannedDir rows `CacheRepository.replace_subtree`
    writes.

    `du` doesn't tag its output with file-vs-directory or a per-directory
    item count, so both are derived here:
    - symlinks: `du` doesn't follow them (no `-L`/`-D` passed), but `-a`
      still prints the link itself as its own tiny entry - excluded here
      via `os.path.islink`, same "skip symlinks, don't size them" rule
      `list_immediate` applies, and BEFORE the is_dir/item_count steps
      below: a symlink to a directory would otherwise stat as `is_dir`
      True with the *link's* tiny size, not the target's, silently
      corrupting that "directory"'s total.
    - is_dir: a plain `os.path.isdir` stat per entry - negligible next to
      the directory walk `du` itself already paid for.
    - item_count (files anywhere recursively under a directory): computed
      bottom-up in one pass over paths ordered deepest-first via a
      parent->children index, so every child's count is already known by
      the time its parent is processed - O(n) total, no repeated
      substring/prefix scanning per directory.
    """
    sizes: Dict[str, int] = {}
    skipped = 0
    for path, size in pairs:
        if os.path.islink(path):
            skipped += 1
            continue
        sizes[path] = size
    is_dir: Dict[str, bool] = {path: os.path.isdir(path) for path in sizes}

    children_by_parent: Dict[str, List[str]] = {}
    for path in sizes:
        children_by_parent.setdefault(os.path.dirname(path), []).append(path)

    item_count: Dict[str, int] = {}
    for path in sorted(sizes, key=lambda p: p.count(os.sep), reverse=True):
        if is_dir[path]:
            item_count[path] = sum(item_count.get(child, 0) for child in children_by_parent.get(path, []))
        else:
            item_count[path] = 1

    files: List[ScannedFile] = []
    dirs: List[ScannedDir] = []
    for path, size in sizes.items():
        parent = os.path.dirname(path)
        if is_dir[path]:
            dirs.append(ScannedDir(path=path, parent_path=parent, size_bytes=size, item_count=item_count[path]))
        else:
            _, ext = os.path.splitext(os.path.basename(path))
            files.append(
                ScannedFile(path=path, parent_path=parent, size_bytes=size, extension=ext.lstrip(".").lower())
            )

    return SubtreeScan(root_path=root_path, files=files, dirs=dirs, warnings=warnings, skipped=skipped)


class DiskScanRepository:
    """The one place in this app that shells out to `du` or walks the
    filesystem directly - `CacheRepository` never touches either, it only
    reads/writes the SQLite cache these methods' results get stored into."""

    def __init__(self, timeout: int = DEFAULT_SCAN_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def list_immediate(self, folder_path: str) -> ImmediateListing:
        """Cheap, non-recursive `os.scandir` pass over `folder_path`'s own
        direct entries - identity only, no `du` call, mirrors
        `DiskUsageRepository.list_targets`'s "loads automatically, no
        Calculate needed" cheap half in my-docker-viewer. Direct files are
        fully known from this alone (one `os.stat` each); direct
        subdirectories come back as bare paths only - their totals only
        come from their own `scan_subdirectory` call.

        Symlinks (file or directory) and subdirectories that cross onto a
        different filesystem are skipped entirely, counted in `skipped`
        rather than sized or recursed into - matching `scan_subdirectory`'s
        `-x` behaviour so a folder's total is consistent regardless of
        which half computed it.
        """
        try:
            own_stat = os.stat(folder_path)
        except OSError as exc:
            return ImmediateListing(error=str(exc))

        subdirs: List[str] = []
        files: List[ScannedFile] = []
        skipped = 0
        try:
            with os.scandir(folder_path) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            skipped += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.stat().st_dev != own_stat.st_dev:
                                skipped += 1  # different filesystem, matches du -x
                                continue
                            subdirs.append(entry.path)
                        else:
                            files.append(_stat_file(entry))
                    except OSError:
                        # Vanished mid-listing, or unreadable metadata for
                        # this one entry - skip it rather than fail the
                        # whole folder over one bad entry.
                        skipped += 1
        except OSError as exc:
            return ImmediateListing(subdirs=subdirs, files=files, skipped=skipped, error=str(exc))

        return ImmediateListing(subdirs=subdirs, files=files, skipped=skipped)

    def scan_subdirectory(self, path: str) -> SubtreeScan:
        """Runs `du -a -k -x <path>`, recursively covering every file and
        directory under it in one call (see module docstring) - the
        expensive half, meant to always run as its own independent
        background job per immediate subdirectory (see ExplorerPage, a
        later step), never on a timer or just from opening a folder."""
        stdout, warnings = _run_du(["-a", "-k", "-x", path], timeout=self._timeout)
        pairs = _parse_du_lines(stdout)
        return _build_subtree_scan(path, pairs, warnings)


def _stat_file(entry: "os.DirEntry") -> ScannedFile:
    """Real allocated disk usage for one file, `du`'s own definition
    applied directly via `os.stat` - `st_blocks` is always in 512-byte
    units per POSIX regardless of the filesystem's actual block size, so
    multiplying by 512 gives allocated bytes, not `st_size`'s
    apparent/logical size. Falls back to `st_size` if `st_blocks` isn't
    available (it always is on Linux/macOS; the guard is only here so this
    same helper doesn't need rewriting for a future Windows backend, where
    it isn't)."""
    stat = entry.stat()
    size_bytes = stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
    _, ext = os.path.splitext(entry.name)
    return ScannedFile(path=entry.path, parent_path=os.path.dirname(entry.path), size_bytes=size_bytes, extension=ext.lstrip(".").lower())
