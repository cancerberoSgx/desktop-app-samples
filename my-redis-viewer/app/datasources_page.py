from typing import Callable, List, Optional

import wx

from .async_task import AsyncTaskRunner
from .datasources_dialog import DatasourceDialog
from .models import Datasource
from .repositories import DatasourceRepository


class DatasourcesPage(wx.Panel):
    """CRUD screen for datasources (Redis connections): filter by name,
    create, edit, delete, and "Connect" to PING the server - on success
    this hands off to the Data Explorer (via `on_connected`) instead of
    showing a message box; on failure it still reports the error here."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        profile_id: int,
        on_connected: Optional[Callable[[Datasource], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._profile_id = profile_id
        self._datasources: List[Datasource] = []
        self._on_connected = on_connected or (lambda datasource: None)
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Data Sources"), 0, wx.ALL, 12)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(wx.StaticText(self, label="Name contains:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_filter = wx.SearchCtrl(self, size=(200, -1))
        self._name_filter.ShowCancelButton(True)
        toolbar.Add(self._name_filter, 0, wx.ALIGN_CENTER_VERTICAL)

        toolbar.AddStretchSpacer()

        self._new_btn = wx.Button(self, label="New...")
        self._edit_btn = wx.Button(self, label="Edit...")
        self._delete_btn = wx.Button(self, label="Delete")
        self._connect_btn = wx.Button(self, label="Connect")
        toolbar.Add(self._new_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._edit_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._delete_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._connect_btn, 0)

        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Name", width=180)
        self._list.InsertColumn(1, "Host", width=200)
        self._list.InsertColumn(2, "Port", width=80)
        self._list.InsertColumn(3, "User", width=140)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_filter_cancel)
        self._new_btn.Bind(wx.EVT_BUTTON, self._on_new)
        self._edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._connect_btn.Bind(wx.EVT_BUTTON, self._on_connect)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_connect)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)

        self.reload()

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self.reload()

    def reload(self) -> None:
        name_contains = self._name_filter.GetValue().strip() or None
        self._datasources = self._repository.list(self._profile_id, name_contains=name_contains)

        self._list.DeleteAllItems()
        for row, datasource in enumerate(self._datasources):
            self._list.InsertItem(row, datasource.name)
            self._list.SetItem(row, 1, datasource.redis_host)
            self._list.SetItem(row, 2, str(datasource.redis_port))
            self._list.SetItem(row, 3, datasource.redis_user or "")

        self._update_button_states(None)

    def _selected_datasource(self) -> Optional[Datasource]:
        index = self._list.GetFirstSelected()
        if index == -1:
            return None
        return self._datasources[index]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        has_selection = self._selected_datasource() is not None
        self._edit_btn.Enable(has_selection)
        self._delete_btn.Enable(has_selection)
        self._connect_btn.Enable(has_selection)

    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self.reload()

    def _on_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._name_filter.SetValue("")
        self.reload()

    def _on_new(self, event: wx.CommandEvent) -> None:
        dlg = DatasourceDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            datasource = dlg.get_datasource()
            datasource.profile_id = self._profile_id
            self._repository.create(datasource)
            self.reload()
        dlg.Destroy()

    def _on_edit(self, event: wx.CommandEvent) -> None:
        datasource = self._selected_datasource()
        if datasource is None:
            return
        dlg = DatasourceDialog(self, datasource)
        if dlg.ShowModal() == wx.ID_OK:
            self._repository.update(dlg.get_datasource())
            self.reload()
        dlg.Destroy()

    def _on_delete(self, event: wx.CommandEvent) -> None:
        datasource = self._selected_datasource()
        if datasource is None:
            return
        confirm = wx.MessageBox(
            f'Delete data source "{datasource.name}"?',
            "Confirm delete",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._repository.delete(datasource.id)
            self.reload()

    def _on_connect(self, event: wx.CommandEvent) -> None:
        datasource = self._selected_datasource()
        if datasource is None:
            return

        def on_success(_result: None) -> None:
            self._on_connected(datasource)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not connect to "{datasource.name}":\n\n{exc}',
                "Connection failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.test_connection(datasource),
            on_success=on_success,
            on_error=on_error,
            disable=[self._connect_btn],
        )
