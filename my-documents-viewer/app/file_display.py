import os
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from .models import Document

# Kept dependency-free (no wx import) so this can be used from the
# repository layer as well as the UI - see repositories.SettingsRepository.
# get_file_name_display/set_file_name_display.

FULL_PATH = "full_path"
FILE_NAME = "file_name"
PARENT_AND_FILE = "parent_and_file"

FILE_NAME_DISPLAY_DEFAULT = FILE_NAME

FILE_NAME_DISPLAY_OPTIONS: List[Tuple[str, str]] = [
    (FULL_PATH, "Full path"),
    (FILE_NAME, "File name"),
    (PARENT_AND_FILE, "Parent folder / file name"),
]

FILE_NAME_DISPLAY_KEYS = [key for key, _label in FILE_NAME_DISPLAY_OPTIONS]


def format_display_path(path: str, mode: str) -> str:
    """Render `path` for display per the "File name display" setting
    (see Settings dialog / SettingsRepository.get_file_name_display).

    The raw path is always kept around separately (e.g. for hover tooltips
    or opening the file) - this only controls what a list row shows.
    Falls back to the full path for an unrecognized mode."""
    if mode == FILE_NAME:
        return os.path.basename(path) or path
    if mode == PARENT_AND_FILE:
        name = os.path.basename(path) or path
        parent = os.path.basename(os.path.dirname(path))
        return f"{parent}/{name}" if parent else name
    return path


def format_document_label(document: "Document", container: Optional["Document"], mode: str) -> str:
    """Display label for a Document row. Unchanged for a plain file or a
    container (format_display_path over its real path) - but a record's
    `path` is a synthetic string DocumentRepository generates purely to
    keep it globally unique (see migration 0006's comment), never meant to
    be shown, so a record instead shows its container's label plus its own
    title/row_key. `container` is the record's parent Document (None for a
    file/container row, or when the caller doesn't have it handy - falls
    back to the raw path in that case)."""
    if document.kind != "record" or container is None:
        return format_display_path(document.path, mode)

    container_label = format_display_path(container.path, mode)
    title_column = (container.properties or {}).get("title_column")
    title = (document.properties or {}).get(title_column) if title_column else None
    row_label = str(title) if title not in (None, "") else (document.row_key or f"record {document.id}")
    return f"{container_label} › {row_label}"
