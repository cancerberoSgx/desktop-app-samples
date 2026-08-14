from typing import List, Tuple

import wx

MAX_DISPLAY_LEN = 20


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_DISPLAY_LEN else text[:MAX_DISPLAY_LEN] + "..."


class HashTableCtrl(wx.ListCtrl):
    """Field/value table for a hash key's Table tab (see
    KeyDetailsDialog). Values longer than MAX_DISPLAY_LEN chars are
    truncated for display only - selecting a row and pressing Ctrl+C
    copies that row's full, untruncated value to the clipboard."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.InsertColumn(0, "Field", width=200)
        self.InsertColumn(1, "Value", width=380)
        self._values: List[str] = []
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)

    def set_fields(self, fields: List[Tuple[str, str]]) -> None:
        self.DeleteAllItems()
        self._values = [value for _field, value in fields]
        for row, (field, value) in enumerate(fields):
            self.InsertItem(row, field)
            self.SetItem(row, 1, _truncate(value))

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.ControlDown() and event.GetKeyCode() == ord("C"):
            self._copy_selected_value()
        else:
            event.Skip()

    def _copy_selected_value(self) -> None:
        row = self.GetFirstSelected()
        if row == -1:
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(self._values[row]))
            wx.TheClipboard.Close()
