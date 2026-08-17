import time
from typing import Optional

"""Shared display helpers - split into their own module the same way
my-disk-viewer's/my-docker-viewer's app/formatting.py is, once more than one
screen needs the same byte-count/timestamp -> human string rendering."""


def format_bytes(num_bytes: Optional[int]) -> str:
    """Human-friendly decimal size for a raw byte count. `None` (a
    directory - recursive size isn't computed yet, see FileEntry) renders as
    "-" rather than "0 B", so an unsized folder can't be misread as an empty
    one."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def format_timestamp(epoch_seconds: Optional[float]) -> str:
    """Human-readable local timestamp for an `os.stat()` epoch value
    (`st_mtime`/`st_ctime`/`st_atime`/a platform "birth time"). `None`
    renders as "-" - either the entry could not be stat-ed (see
    FileSystemService.list_folder's `skipped` count) or, for a creation
    date on a platform/filesystem with no reliable one (see
    FileSystemService.get_properties), there simply isn't a value to
    show."""
    if epoch_seconds is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch_seconds))
