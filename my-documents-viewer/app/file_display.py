import os
from typing import List, Tuple

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
