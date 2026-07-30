from typing import List

import wx

from .key_details_dialog import KeyDetailsDialog
from .key_list_ctrl import KeyListCtrl
from .models import Datasource
from .repositories import DatasourceRepository


class KeyListDialog(wx.Dialog):
    """Browse a fixed list of key names in a table, same as the Data
    Explorer's Tree/Search tabs - double-clicking (or Enter on) a row
    opens KeyDetailsDialog. Self-contained: any screen that already has a
    list of key names (currently the Scripts tab, for a KEYS command's
    result) can open this with just that list plus a repository and
    datasource."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        datasource: Datasource,
        keys: List[str],
        title: str = "Keys",
    ) -> None:
        super().__init__(
            parent,
            title=title,
            size=(480, 420),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._repository = repository
        self._datasource = datasource

        sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = KeyListCtrl(self, on_activate_key=self._on_activate_key)
        self._list.set_keys(sorted(keys))
        sizer.Add(self._list, 1, wx.EXPAND | wx.ALL, 12)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(self, id=wx.ID_CLOSE, label="Close")
        button_sizer.AddStretchSpacer()
        button_sizer.Add(close_btn, 0)
        sizer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(sizer)
        close_btn.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))

    def _on_activate_key(self, key: str) -> None:
        dlg = KeyDetailsDialog(self, self._repository, self._datasource, key)
        dlg.ShowModal()
        dlg.Destroy()
