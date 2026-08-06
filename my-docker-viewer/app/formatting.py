import re
from typing import Optional, Set

from .models import Volume

"""Shared display/sort helpers used by more than one list page - split out
of containers_page.py once ImagesPage needed the same size-string parsing,
so a size column added to a future page (Volumes, ...) doesn't mean
re-deriving this again."""

# Byte-unit multipliers, covering both docker's decimal (kB/MB/...) and
# binary (KiB/MiB/...) size suffixes.
_SIZE_UNITS = {
    "B": 1,
    "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
    "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4,
}
_SIZE_RE = re.compile(r"([\d.]+)\s*([A-Za-z]+)")


def size_sort_key(text: Optional[str]) -> float:
    """Parses a leading docker size/usage figure (`"15.5MiB / 1.9GiB"`,
    `"0B (virtual 435MB)"`, `"119MB"`) into bytes for numeric column
    sorting; anything unparsable (including None) sorts lowest."""
    if not text:
        return -1.0
    match = _SIZE_RE.match(text.strip())
    if not match:
        return -1.0
    number, unit = match.groups()
    try:
        value = float(number)
    except ValueError:
        return -1.0
    return value * _SIZE_UNITS.get(unit.upper(), 1)


def format_bytes(num_bytes: int) -> str:
    """Human-friendly decimal size for a raw byte count *we* computed (via
    `du`, never parsed from docker's own size strings - see `size_sort_key`
    for that direction). Originally lived in containers_disk_page.py;
    moved here once volumes_page.py needed the same formatting for its own
    on-demand Size column."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000


def volume_size_text(volume: Volume, pending: Set[str]) -> str:
    """Renders a Volume's on-demand Size state - "Not calculated" until
    Calculate has run, "Calculating..." while `volume.name` is in
    `pending`, the human-readable total once known, or the error if sizing
    failed. Originally lived in volumes_page.py as its Size column
    renderer; moved here once volume_details_dialog.py needed the exact
    same state->text mapping for a single volume (pass `{volume.name}` as
    `pending` while its own Calculate is running, or an empty set
    otherwise)."""
    if volume.size_error is not None:
        return f"Error: {volume.size_error}"
    if volume.name in pending:
        return "Calculating..."
    if volume.size_bytes is None:
        return "Not calculated"
    return format_bytes(volume.size_bytes)
