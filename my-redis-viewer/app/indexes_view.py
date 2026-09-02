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
        self._delete_btn = wx.Button(self, label="Delete")
        self._delete_btn.Enable(False)
        toolbar.Add(self._delete_btn, 0, wx.RIGHT, 8)
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
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._filter_ctrl.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._filter_ctrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_filter_cancel)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)

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
        self._delete_btn.Enable(False)

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
        self._update_button_states(None)

    def _selected_index_name(self) -> Optional[str]:
        row = self._list.GetFirstSelected()
        if row == -1:
            return None
        return self._displayed_names[row]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        self._delete_btn.Enable(self._selected_index_name() is not None)

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

    def _on_delete(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        index_name = self._selected_index_name()
        if index_name is None:
            return

        dlg = wx.MessageDialog(
            self,
            f'Delete RediSearch index "{index_name}"?\n\n'
            "Delete Index Only removes just the index definition - the hash/JSON "
            "keys it covers are left alone. Delete Index and Data also deletes "
            "every one of those keys.",
            "Delete index",
            wx.YES_NO | wx.CANCEL | wx.ICON_WARNING,
        )
        # Custom labels only rename the buttons - ShowModal() still returns
        # the underlying wx.ID_YES/ID_NO/ID_CANCEL for whichever was clicked.
        dlg.SetYesNoCancelLabels("Delete Index Only", "Delete Index and Data", "Cancel")
        result = dlg.ShowModal()
        dlg.Destroy()
        if result == wx.ID_CANCEL:
            return
        delete_data = result == wx.ID_NO

        datasource = self._datasource

        def on_success(_result: None) -> None:
            # Drop it from the local cache and re-render instead of calling
            # reload(): that would re-run list_indexes() through this same
            # AsyncTaskRunner, but its "busy" flag isn't cleared until after
            # this callback returns, so a nested run() call here would be
            # silently ignored - the list would keep showing the deleted
            # index until the user pressed Refresh themselves.
            if index_name in self._index_names:
                self._index_names.remove(index_name)
            self._apply_filter()

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not delete index "{index_name}":\n\n{exc}',
                "Delete index failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.delete_index(datasource, index_name, delete_data),
            on_success=on_success,
            on_error=on_error,
            disable=[self._refresh_btn, self._delete_btn],
        )
