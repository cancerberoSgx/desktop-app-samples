from typing import List, Optional, Tuple

import wx

from .async_task import AsyncTaskRunner
from .models import Network
from .network_details_dialog import show_network_details
from .repositories import DockerCommandError, DockerNotAvailableError, NetworkRepository

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
    of their own.

    Like VolumesPage, the list is multi-select (`wx.LC_REPORT` without
    `wx.LC_SINGLE_SEL`) - ctrl-click/shift-click and shift+Up/Down are
    wx.ListCtrl's own native selection behavior, nothing custom here.
    Remove (and the Delete key, bound as its shortcut) acts on the whole
    selection: a builtin or in-use network is skipped with an explanation
    rather than blocking the removable ones too, same "explain before it
    fails" posture as the single-network case always had. Info only makes
    sense for one network at a time, so it stays disabled unless the
    selection is exactly one row."""

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

        # No wx.LC_SINGLE_SEL - this list is deliberately multi-select (see
        # the class docstring), same as VolumesPage.
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
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
        # Delete key as a shortcut for Remove, covering the whole selection
        # same as clicking the button would - mirrors VolumesPage.
        self._list.Bind(wx.EVT_LIST_KEY_DOWN, self._on_list_key_down)

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
        # Preserve the whole selection, not just one row - a resort/filter/
        # refresh mid multi-select shouldn't collapse it down to one item.
        selected_names = {n.name for n in self._selected_networks()}

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
            if network.name in selected_names:
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
        """The single selected network - for actions (Info) that only make
        sense against exactly one row. Returns `None` for zero *or* more
        than one selected, unlike `_selected_networks()` below."""
        networks = self._selected_networks()
        return networks[0] if len(networks) == 1 else None

    def _selected_networks(self) -> List[Network]:
        """Every currently selected network, in list order - this is a
        multi-select list (see the class docstring), so callers that act on
        "the selection" (Remove) should use this, not `_selected_network()`."""
        networks = []
        index = self._list.GetFirstSelected()
        while index != -1:
            if index < len(self._visible):
                networks.append(self._visible[index])
            index = self._list.GetNextSelected(index)
        return networks

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        networks = self._selected_networks()
        # Info only makes sense for exactly one network at a time.
        self._info_btn.Enable(len(networks) == 1)
        # Predefined networks (bridge/host/none) can never be removed -
        # the button stays disabled if that's all that's selected, same
        # "explain before it fails" posture as VolumesPage's in-use check.
        # A mixed selection still enables it - _on_remove partitions out
        # the builtin/in-use ones and explains those separately.
        self._remove_btn.Enable(any(not n.is_builtin for n in networks))

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
        # Enter on a row (wx.EVT_LIST_ITEM_ACTIVATED, a wx.ListEvent) - the
        # activation event names the exact row that was double-clicked/
        # entered, which - unlike _selected_network() - still resolves to
        # one network even if a multi-select happens to include others.
        if isinstance(event, wx.ListEvent):
            index = event.GetIndex()
            network = self._visible[index] if 0 <= index < len(self._visible) else None
        else:
            network = self._selected_network()
        if network is None:
            return
        show_network_details(self, network.name, self._repository, initial=network)

    def _on_list_key_down(self, event: wx.ListEvent) -> None:
        if event.GetKeyCode() == wx.WXK_DELETE:
            self._on_remove(event)
        else:
            event.Skip()

    def _apply_removed(self, names: List[str]) -> None:
        """Mirrors VolumesPage._apply_removed - drop the given networks
        from the already-loaded list and re-render immediately instead of
        waiting on a full `docker network ls` round trip. Takes a list (not
        one name) so a multi-select removal renders as a single batch
        rather than one re-render per network."""
        removed = set(names)
        self._networks = [n for n in self._networks if n.name not in removed]
        self._populate_list()

    def _on_remove(self, event: wx.Event) -> None:
        networks = self._selected_networks()
        if not networks:
            return

        # Neither builtin (bridge/host/none) nor in-use networks can be
        # removed - docker refuses outright regardless of flags, so this
        # explains why up front instead of firing calls guaranteed to fail.
        # Removable networks still go ahead rather than the whole selection
        # being blocked by the ones that can't, same posture as
        # VolumesPage's in-use partitioning.
        removable = [n for n in networks if not n.is_builtin and not n.is_in_use]
        blocked = [n for n in networks if n.is_builtin or n.is_in_use]

        if not blocked:
            prompt = (
                f'Remove network "{removable[0].name}"?'
                if len(removable) == 1
                else f"Remove {len(removable)} networks?"
            )
        elif not removable:
            noun = "Network" if len(blocked) == 1 else "All selected networks"
            verb = "is" if len(blocked) == 1 else "are"
            wx.MessageBox(
                f"{noun} {verb} predefined or in use and can't be removed: "
                f'{", ".join(n.name for n in blocked)}.\n\n'
                "Docker never removes bridge/host/none, and won't remove a "
                "network that's in use - remove or disconnect those "
                "containers first.",
                "Cannot remove",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        else:
            prompt = (
                f"Remove {len(removable)} of {len(networks)} selected networks?\n\n"
                f"{len(blocked)} will be skipped - predefined or in use: "
                f'{", ".join(n.name for n in blocked)}.'
            )

        confirm = wx.MessageBox(prompt, "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        names = [n.name for n in removable]

        def work() -> List[Tuple[str, Optional[str]]]:
            # One `docker network rm` per name, continuing past an
            # individual failure rather than aborting the whole batch over
            # one bad network - same posture as VolumesPage's batch remove.
            results = []
            for name in names:
                try:
                    self._repository.remove(name)
                    results.append((name, None))
                except (DockerCommandError, DockerNotAvailableError) as exc:
                    results.append((name, str(exc)))
            return results

        def on_success(results: List[Tuple[str, Optional[str]]]) -> None:
            succeeded = [name for name, error in results if error is None]
            failed = [(name, error) for name, error in results if error is not None]
            if succeeded:
                self._apply_removed(succeeded)
            if failed:
                details = "\n".join(f'"{name}": {error}' for name, error in failed)
                wx.MessageBox(
                    f"Could not remove {len(failed)} of {len(names)} network(s):\n\n{details}",
                    "Remove failed",
                    wx.OK | wx.ICON_ERROR,
                    self,
                )

        def on_error(exc: Exception) -> None:
            # Only reachable for a truly unexpected failure - work() itself
            # catches both known docker exception types per-item above.
            wx.MessageBox(f"Could not remove networks:\n\n{exc}", "Remove failed", wx.OK | wx.ICON_ERROR, self)

        self._async.run(
            work=work,
            on_success=on_success,
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
