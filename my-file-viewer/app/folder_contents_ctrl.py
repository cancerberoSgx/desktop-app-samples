from typing import Callable, List, Optional

import wx

from .formatting import format_bytes, format_modified
from .models import FileEntry

# Column indices, index-aligned to _COLUMNS and _SORT_KEYS below.
COL_NAME, COL_SIZE, COL_MODIFIED = range(3)
_COLUMNS = [
    ("Name", 360),
    ("Size", 110),
    ("Modified", 160),
]

# Sort key per column - each returns the raw (unformatted) value to compare,
# so e.g. Size sorts numerically, not on the "12.3 KB" display string, and
# Modified sorts on the epoch float, not the formatted date string.
_SORT_KEYS: List[Callable[[FileEntry], object]] = [
    lambda e: e.name.lower(),
    lambda e: -1 if e.size_bytes is None else e.size_bytes,
    lambda e: -1.0 if e.modified_at is None else e.modified_at,
]


class FolderContentsCtrl(wx.ListCtrl):
    """Virtual list of the current folder's immediate children - virtual
    mode (LC_VIRTUAL) keeps this responsive for a very large folder, since
    no per-row wx item is ever created; only OnGetItemText is called, and
    only for rows actually on screen. Columns (Name, Size, Modified) are
    click-to-sort, ascending/descending toggling on repeat clicks - more
    columns (file type, glob-matched, recursive folder size, ...) are meant
    to be added here later, see CLAUDE.md.

    Folders are always grouped before files (the usual file-explorer
    convention), sorted by the active column within each group - so sorting
    by Size still shows "which folder/file is biggest" without folders and
    files interleaving.
    """

    def __init__(self, parent: wx.Window, on_activate_entry: Callable[[FileEntry], None]) -> None:
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self.InsertColumn(index, label, width=width)

        self._entries: List[FileEntry] = []
        self._visible: List[FileEntry] = []
        self._sort_column = COL_NAME
        self._sort_ascending = True
        self._on_activate_entry = on_activate_entry

        self._update_column_headers()
        self.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)

    def set_entries(self, entries: List[FileEntry]) -> None:
        """Replace the folder contents shown, keeping whatever sort column/
        direction the user last picked."""
        self._entries = entries
        self._resort()

    def get_selected_entry(self) -> Optional[FileEntry]:
        index = self.GetFirstSelected()
        if index == -1 or index >= len(self._visible):
            return None
        return self._visible[index]

    # ------------------------------------------------------------------
    # wx.LC_VIRTUAL overrides
    # ------------------------------------------------------------------
    def OnGetItemText(self, item: int, column: int) -> str:  # noqa: N802 - wx override
        entry = self._visible[item]
        if column == COL_NAME:
            return f"{'📁' if entry.is_dir else '📄'} {entry.name}"
        if column == COL_SIZE:
            return format_bytes(entry.size_bytes)
        if column == COL_MODIFIED:
            return format_modified(entry.modified_at)
        return ""

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    def _on_col_click(self, event: wx.ListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._update_column_headers()
        self._resort()

    def _resort(self) -> None:
        selected = self.get_selected_entry()
        selected_path = selected.path if selected else None

        # Two stable passes rather than one sort keyed on (group, value):
        # `reverse=` would flip the group order too (files before folders)
        # on a descending sort, which folders-first-always must not do.
        # Sorting by the group second relies on Python's sort being stable,
        # so it only reorders across groups, never within one.
        key_func = _SORT_KEYS[self._sort_column]
        rows = sorted(self._entries, key=key_func, reverse=not self._sort_ascending)
        rows.sort(key=lambda e: 0 if e.is_dir else 1)
        self._visible = rows
        self.SetItemCount(len(rows))

        if selected_path:
            for row, entry in enumerate(rows):
                if entry.path == selected_path:
                    self.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
                    break
        self.Refresh()

    def _update_column_headers(self) -> None:
        for index, label in enumerate(self._column_labels):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            column_info = self.GetColumn(index)
            column_info.SetText(label)
            self.SetColumn(index, column_info)

    # ------------------------------------------------------------------
    # Activation (double-click / Enter)
    # ------------------------------------------------------------------
    def _on_item_activated(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if 0 <= index < len(self._visible):
            self._on_activate_entry(self._visible[index])
