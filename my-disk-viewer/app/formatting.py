from typing import Optional

"""Shared display helpers - split into their own module the same way
my-docker-viewer's app/formatting.py is, once more than one screen needs
the same byte-count -> human string rendering."""


def format_bytes(num_bytes: Optional[int]) -> str:
    """Human-friendly decimal size for a raw byte count already sitting in
    the cache (`folders.total_bytes`/`files.size_bytes` - always real
    bytes by the time they're stored, see `DiskScanRepository`, never a
    size *string* that needs parsing the way docker's own output does).
    `None` (not yet scanned) renders as "-" rather than "0 B", so an unsized
    folder can't be misread as an empty one."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"
