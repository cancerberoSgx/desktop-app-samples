from typing import Callable, List, Optional

import wx

from .datasources_dialog import DatasourceDialog
from .models import DATASOURCE_TYPES, Datasource
from .repositories import DatasourceRepository


def _details_for(datasource: Datasource) -> str:
    if datasource.type in ("csv", "json"):
        return datasource.file_path or ""
    host = datasource.db_host or ""
    port = f":{datasource.db_port}" if datasource.db_port else ""
    name = datasource.db_name or ""
    return f"{host}{port}/{name}".strip("/")


class DatasourcesPage(wx.Panel):
    """CRUD screen for datasources: filter by name/type, create, edit, delete."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        profile_id: int,
        on_connected: Callable[[Datasource], None],
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._profile_id = profile_id
        self._on_connected = on_connected
        self._datasources: List[Datasource] = []

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Datasources"), 0, wx.ALL, 12)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(wx.StaticText(self, label="Name contains:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_filter = wx.SearchCtrl(self, size=(200, -1))
        self._name_filter.ShowCancelButton(True)
        toolbar.Add(self._name_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        toolbar.Add(wx.StaticText(self, label="Type:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._type_filter = wx.Choice(self, choices=["All"] + list(DATASOURCE_TYPES))
        self._type_filter.SetSelection(0)
        toolbar.Add(self._type_filter, 0, wx.ALIGN_CENTER_VERTICAL)

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
        self._list.InsertColumn(1, "Type", width=100)
        self._list.InsertColumn(2, "Details", width=320)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_filter_cancel)
        self._type_filter.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._new_btn.Bind(wx.EVT_BUTTON, self._on_new)
        self._edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._connect_btn.Bind(wx.EVT_BUTTON, self._on_connect)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_edit)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)

        self.reload()

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self.reload()

    def reload(self) -> None:
        name_contains = self._name_filter.GetValue().strip() or None
        type_index = self._type_filter.GetSelection()
        type_ = None if type_index <= 0 else self._type_filter.GetString(type_index)

        self._datasources = self._repository.list(
            self._profile_id, name_contains=name_contains, type_=type_
        )

        self._list.DeleteAllItems()
        for row, datasource in enumerate(self._datasources):
            self._list.InsertItem(row, datasource.name)
            self._list.SetItem(row, 1, datasource.type)
            self._list.SetItem(row, 2, _details_for(datasource))

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
        fields = self._repository.list_fields(datasource.id) if datasource.type in ("csv", "json") else []
        dlg = DatasourceDialog(self, datasource, fields=fields)
        if dlg.ShowModal() == wx.ID_OK:
            self._repository.update(dlg.get_datasource())
            self.reload()
        dlg.Destroy()

    def _on_delete(self, event: wx.CommandEvent) -> None:
        datasource = self._selected_datasource()
        if datasource is None:
            return
        confirm = wx.MessageBox(
            f'Delete datasource "{datasource.name}"?',
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
        try:
            self._repository.test_connection(datasource)
        except Exception as exc:
            wx.MessageBox(
                f'Could not connect to "{datasource.name}":\n\n{exc}',
                "Connection failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        self._on_connected(datasource)
