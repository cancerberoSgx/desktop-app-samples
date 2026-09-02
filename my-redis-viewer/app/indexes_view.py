from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .index_details_dialog import IndexDetailsDialog
from .models import Datasource
from .repositories import DatasourceRepository


class IndexesView(wx.Panel):
    """Lists every RediSearch index on the connected server (FT._LIST).
    Activating (double-click/Enter on) a row opens IndexDetailsDialog,
    which does the heavier FT.INFO round-trip lazily - mirrors the
    Tree tab's KeyListCtrl -> KeyDetailsDialog split."""

    def __init__(self, parent: wx.Window, repository: DatasourceRepository) -> None:
        super().__init__(parent)
        self._repository = repository
        self._datasource: Optional[Datasource] = None
        self._index_names: List[str] = []
        self._displayed_names: List[str] = []
        self._async = AsyncTaskRunner(self)

        sizer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        toolbar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        self._filter_ctrl = wx.SearchCtrl(self, size=(200, -1))
        self._filter_ctrl.SetDescriptiveText("Filter indexes")
        self._filter_ctrl.ShowCancelButton(True)
        toolbar.Add(self._filter_ctrl, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        self._status = wx.StaticText(self, label="")
        toolbar.Add(self._status, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Index name", width=400)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(sizer)

        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._filter_ctrl.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._filter_ctrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_filter_cancel)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)

    def set_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self.reload()

    def clear(self) -> None:
        self._datasource = None
        self._index_names = []
        self._displayed_names = []
        self._filter_ctrl.ChangeValue("")
        self._list.DeleteAllItems()
        self._status.SetLabel("")

    def reload(self) -> None:
        if self._datasource is None:
            return
        datasource = self._datasource
        self._status.SetLabel("Loading indexes...")

        def on_success(names: List[str]) -> None:
            self._index_names = names
            self._apply_filter()

        def on_error(exc: Exception) -> None:
            self._status.SetLabel("Could not list indexes")
            wx.MessageBox(
                f'Could not list indexes on "{datasource.name}":\n\n{exc}',
                "Index listing failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.list_indexes(datasource),
            on_success=on_success,
            on_error=on_error,
            disable=[self._refresh_btn],
        )

    def _apply_filter(self) -> None:
        """Re-render `_list` from the already-fetched `_index_names` cache,
        keeping only names containing the filter text (case-insensitive).
        Client-side only - never re-queries Redis."""
        query = self._filter_ctrl.GetValue().strip().lower()
        names = self._index_names
        self._displayed_names = (
            names if not query else [name for name in names if query in name.lower()]
        )

        self._list.DeleteAllItems()
        for row, name in enumerate(self._displayed_names):
            self._list.InsertItem(row, name)

        total = len(names)
        shown = len(self._displayed_names)
        noun = "index" if total == 1 else "indexes"
        if query:
            self._status.SetLabel(f"{shown:,} of {total:,} {noun}")
        else:
            self._status.SetLabel(f"{total:,} {noun}")

    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self._apply_filter()

    def _on_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._filter_ctrl.ChangeValue("")
        self._apply_filter()

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self.reload()

    def _on_item_activated(self, event: wx.ListEvent) -> None:
        if self._datasource is None:
            return
        index_name = self._displayed_names[event.GetIndex()]
        dlg = IndexDetailsDialog(self, self._repository, self._datasource, index_name)
        dlg.ShowModal()
        dlg.Destroy()
