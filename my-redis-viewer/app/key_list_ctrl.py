from typing import Callable, List, Optional

import wx


class KeyListCtrl(wx.ListCtrl):
    """Virtual list of key names - virtual mode keeps this responsive even
    for a very large key list, since no per-row wx item is ever created.
    Double-clicking (or Enter on) a row calls `on_activate_key`. Used by
    the Data Explorer's Tree/Search tabs and by KeyListDialog."""

    def __init__(self, parent: wx.Window, on_activate_key: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.BORDER_SUNKEN)
        self.InsertColumn(0, "Key", width=420)
        self._keys: List[str] = []
        self._on_activate_key = on_activate_key or (lambda key: None)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)

    def set_keys(self, keys: List[str]) -> None:
        self._keys = keys
        self.SetItemCount(len(keys))
        self.Refresh()

    def OnGetItemText(self, item: int, column: int) -> str:  # noqa: N802 - wx override
        return self._keys[item]

    def _on_item_activated(self, event: wx.ListEvent) -> None:
        self._on_activate_key(self._keys[event.GetIndex()])
