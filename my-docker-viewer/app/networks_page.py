from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .models import Network
from .network_details_dialog import show_network_details
from .repositories import NetworkRepository

STATUS_CHOICES = ["All", "In use", "Unused"]

# (header label, initial width) per list column, and the matching sort-key
# function - both index-aligned to the columns as inserted into the wx.ListCtrl.
_COLUMNS = [
    ("Name", 220),
    ("Network ID", 110),
    ("Driver", 90),
    ("Scope", 90),
    ("Containers", 90),
    ("Status", 90),
]
_SORT_KEYS = [
    lambda n: n.name.lower(),
    lambda n: n.id,
    lambda n: n.driver.lower(),
    lambda n: n.scope.lower(),
    lambda n: n.containers,
    lambda n: n.status,
]


class NetworksPage(wx.Panel):
    """List every local docker network - driver, scope, and how many
    containers (running or stopped) are attached - filter by name/status,
    remove one, or prune every unused network at once.

    No auto-refresh timer here either, same reasoning as ImagesPage: a
    network list only changes when something actually creates/removes one,
    not on a clock, so a manual Refresh is enough.

    Info (or double-clicking/pressing Enter on a row) opens
    `NetworkDetailsDialog` (`app/network_details_dialog.py`) for the
    selected network - driver/scope/built-in status and the full
    (untruncated) list of attached containers. That dialog is its own
    reusable component precisely so other screens can open the same view
    later from just a network name, without needing a loaded `Network` row
    of their own."""

    def __init__(self, parent: wx.Window, repository: NetworkRepository) -> None:
        super().__init__(parent)
        self._repository = repository
        self._networks: List[Network] = []
        self._visible: List[Network] = []
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self._error_text.Hide()

        # Row 1: actions.
        actions_bar = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        self._info_btn = wx.Button(self, label="Info")
        self._remove_btn = wx.Button(self, label="Remove")
        self._prune_btn = wx.Button(self, label="Prune unused")
        actions_bar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._info_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._remove_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._prune_btn, 0)

        actions_bar.AddStretchSpacer()

        self._loading_text = wx.StaticText(self, label="")
        self._loading_text.SetForegroundColour(wx.Colour(120, 120, 120))
        actions_bar.Add(self._loading_text, 0, wx.ALIGN_CENTER_VERTICAL)

        outer.Add(actions_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        # Row 2: filters.
        filters_bar = wx.BoxSizer(wx.HORIZONTAL)
        filters_bar.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_filter = wx.SearchCtrl(self, size=(160, -1))
        self._name_filter.ShowCancelButton(True)
        filters_bar.Add(self._name_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        filters_bar.Add(wx.StaticText(self, label="Status:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._status_choice = wx.Choice(self, choices=STATUS_CHOICES)
        self._status_choice.SetSelection(0)
        filters_bar.Add(self._status_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        outer.Add(filters_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        # Sortable columns: repository.list() already returns networks
        # sorted by name, so that's also the initial header sort state.
        self._sort_column = 0
        self._sort_ascending = True

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_name_filter_cancel)
        self._status_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._info_btn.Bind(wx.EVT_BUTTON, self._on_info)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._prune_btn.Bind(wx.EVT_BUTTON, self._on_prune)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        # Double-click (or Enter on a focused row) a network to jump
        # straight to its details, same shortcut ContainersPage gives its
        # own rows - Info is still there on the toolbar for a single click.
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_info)

        self._update_button_states(None)
        self._update_column_headers()
        self.reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._async.is_busy():
            return
        self._set_loading(True)
        self._async.run(
            work=self._repository.list,
            on_success=self._on_loaded,
            on_error=self._on_load_error,
            on_done=lambda: self._set_loading(False),
            disable=[self._refresh_btn],
        )

    def _set_loading(self, loading: bool) -> None:
        self._loading_text.SetLabel("Loading..." if loading else "")
        if loading and not self._networks:
            self._show_loading_placeholder()

    def _show_loading_placeholder(self) -> None:
        self._list.DeleteAllItems()
        self._list.InsertItem(0, "Loading networks...")

    def _on_loaded(self, networks: List[Network]) -> None:
        self._set_error(None)
        self._networks = networks
        self._populate_list()

    def _on_load_error(self, exc: Exception) -> None:
        self._set_error(str(exc))
        self._networks = []
        self._populate_list()

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    # ------------------------------------------------------------------
    # Filtering / rendering
    # ------------------------------------------------------------------
    def _filtered_networks(self) -> List[Network]:
        name = self._name_filter.GetValue().strip().lower()
        status = self._status_choice.GetStringSelection()

        result = []
        for network in self._networks:
            if name and name not in network.name.lower():
                continue
            if status != "All" and network.status != status:
                continue
            result.append(network)
        return result

    def _populate_list(self) -> None:
        selected = self._selected_network()
        selected_name = selected.name if selected else None

        self._visible = self._filtered_networks()
        self._sort_visible()
        self._list.DeleteAllItems()
        for row, network in enumerate(self._visible):
            self._list.InsertItem(row, network.name)
            self._list.SetItem(row, 1, network.id)
            self._list.SetItem(row, 2, network.driver)
            self._list.SetItem(row, 3, network.scope)
            self._list.SetItem(row, 4, str(network.containers))
            self._list.SetItem(row, 5, network.status)
            if selected_name and network.name == selected_name:
                self._list.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

        self._update_button_states(None)

    def _sort_visible(self) -> None:
        key_func = _SORT_KEYS[self._sort_column]
        self._visible.sort(key=key_func, reverse=not self._sort_ascending)

    def _on_col_click(self, event: wx.ListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._update_column_headers()
        self._populate_list()

    def _update_column_headers(self) -> None:
        for index, label in enumerate(self._column_labels):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            column_info = self._list.GetColumn(index)
            column_info.SetText(label)
            self._list.SetColumn(index, column_info)

    def _selected_network(self) -> Optional[Network]:
        index = self._list.GetFirstSelected()
        if index == -1 or index >= len(self._visible):
            return None
        return self._visible[index]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        network = self._selected_network()
        self._info_btn.Enable(network is not None)
        # Predefined networks (bridge/host/none) can never be removed -
        # disabled outright rather than left to fail against docker's own
        # refusal, same "explain before it fails" posture as VolumesPage's
        # in-use check.
        self._remove_btn.Enable(network is not None and not network.is_builtin)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self._populate_list()

    def _on_name_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._name_filter.SetValue("")
        self._populate_list()

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self.reload()

    def _on_info(self, event: wx.Event) -> None:
        # Bound to both the Info button (wx.CommandEvent) and double-click/
        # Enter on a row (wx.EVT_LIST_ITEM_ACTIVATED, a wx.ListEvent) -
        # neither branch below needs anything event-type-specific.
        network = self._selected_network()
        if network is None:
            return
        show_network_details(self, network.name, self._repository, initial=network)

    def _apply_removed(self, name: str) -> None:
        """Mirrors ImagesPage._apply_removed - drop the network from the
        already-loaded list and re-render immediately instead of waiting on
        a full `docker network ls` round trip."""
        self._networks = [n for n in self._networks if n.name != name]
        self._populate_list()

    def _on_remove(self, event: wx.CommandEvent) -> None:
        network = self._selected_network()
        if network is None or network.is_builtin:
            return

        if network.is_in_use:
            # No `-f` override for "network has active endpoints" either -
            # docker refuses outright, so this explains why up front
            # instead of firing a call guaranteed to fail.
            wx.MessageBox(
                f'Network "{network.name}" is used by: {", ".join(network.container_names)}.\n\n'
                "Docker won't remove a network that's in use - remove or "
                "disconnect those containers first.",
                "Cannot remove",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        confirm = wx.MessageBox(
            f'Remove network "{network.name}"?', "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self
        )
        if confirm != wx.YES:
            return

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not remove "{network.name}":\n\n{exc}',
                "Remove failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.remove(network.name),
            on_success=lambda _result: self._apply_removed(network.name),
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )

    def _on_prune(self, event: wx.CommandEvent) -> None:
        confirm = wx.MessageBox(
            "Remove every network not used by any container?", "Confirm prune", wx.YES_NO | wx.ICON_WARNING, self
        )
        if confirm != wx.YES:
            return

        def on_success(summary: str) -> None:
            wx.MessageBox(summary.strip() or "Nothing to remove.", "Prune complete", wx.OK | wx.ICON_INFORMATION, self)
            # See ImagesPage._on_prune's comment on why this has to go
            # through wx.CallAfter rather than calling reload() directly.
            wx.CallAfter(self.reload)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Could not prune networks:\n\n{exc}", "Prune failed", wx.OK | wx.ICON_ERROR, self)

        self._async.run(
            work=lambda: self._repository.prune(),
            on_success=on_success,
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )
