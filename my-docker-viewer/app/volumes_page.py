from typing import List, Optional, Set, Tuple

import wx

from .async_task import AsyncTaskRunner, run_background
from .formatting import volume_size_text
from .models import Volume
from .repositories import DiskUsageRepository, DockerCommandError, DockerNotAvailableError, VolumeRepository
from .volume_details_dialog import show_volume_details

STATUS_CHOICES = ["All", "In use", "Unused"]

# (header label, initial width) per list column, and the matching sort-key
# function - both index-aligned to the columns as inserted into the wx.ListCtrl.
# Size (last column) is deliberately excluded from _SORT_KEYS - see
# _sort_visible, its ordering depends on transient Calculate state
# (pending/error), not just the Volume's own fields.
_COLUMNS = [
    ("Name", 90),
    ("Driver", 90),
    ("Mountpoint", 90),
    ("Containers", 90),
    ("Used By", 220),
    ("Images", 220),
    ("Status", 90),
    ("Size", 100),
]
_SIZE_COLUMN = 7
_SORT_KEYS = [
    lambda v: v.name.lower(),
    lambda v: v.driver.lower(),
    lambda v: v.mountpoint.lower(),
    lambda v: v.containers,
    lambda v: ", ".join(v.container_names).lower(),
    lambda v: ", ".join(v.image_names).lower(),
    lambda v: v.status,
]


# How many names to spell out in the "Used By"/"Images" cells before
# collapsing the rest into a "+N more" count - a volume shared across a
# whole compose stack would otherwise blow the column out to an unreadable
# width. The *sort* key (_SORT_KEYS above) still sorts on the full,
# untruncated join, so this only affects what's rendered.
_NAMES_SHOWN = 2


def _joined(names: List[str]) -> str:
    if not names:
        return "-"
    if len(names) <= _NAMES_SHOWN:
        return ", ".join(names)
    shown = ", ".join(names[:_NAMES_SHOWN])
    remaining = len(names) - _NAMES_SHOWN
    return f"{shown}, … +{remaining} more"


def _size_sort_key(volume: Volume, pending: Set[str]) -> int:
    # Sorting must stay stable regardless of state: errored or
    # still-calculating rows sort lowest so a descending sort ("biggest
    # first") surfaces real numbers first, same reasoning as
    # ContainersDiskPage's _disk_usage_sort_key.
    if volume.size_error is not None or volume.name in pending:
        return -1
    return -1 if volume.size_bytes is None else volume.size_bytes


class VolumesPage(wx.Panel):
    """List every local docker volume - driver, mountpoint, how many
    containers (running or stopped) mount it, and by name which containers
    and which images those containers were run from (so an anonymous-
    looking volume name still says what it's actually for) - filter by
    name/status, remove one, or prune every unused volume at once. Size is
    computed on demand via Calculate, reusing the exact same helper-container `du`
    approach as the Containers Disk screen (`DiskUsageRepository`) - not
    run automatically on load or on every Refresh, since sizing every
    volume on the machine is comparably expensive to that screen's own
    Calculate (one throwaway container per volume) and this page can list
    far more volumes than a typical container count.

    No auto-refresh timer here either, same reasoning as ImagesPage: a
    volume list only changes when something actually creates/removes one,
    not on a clock, so a manual Refresh is enough.

    Unlike every other screen in this app, the list is multi-select
    (`wx.LC_REPORT` without `wx.LC_SINGLE_SEL`) - ctrl-click/shift-click and
    shift+Up/Down are wx.ListCtrl's own native selection behavior, nothing
    custom here. Remove (and the Delete key, bound as its shortcut) acts on
    the whole selection: any volume that's in use is skipped with an
    explanation rather than blocking the removable ones too, same "explain
    before it fails" posture as the single-volume case always had.

    Info (and double-clicking/pressing Enter on a row) only makes sense for
    one volume at a time, so it's disabled whenever the selection isn't
    exactly one row; it opens
    `VolumeDetailsDialog` (`app/volume_details_dialog.py`) for the selected
    volume - driver/mountpoint/scope, the full Used By/Images lists (not
    truncated the way the table's own columns are), and its size, computed
    the same on-demand way as this page's own Size column. That dialog is
    its own reusable component precisely so other screens can open the same
    view later from just a volume name, without needing a loaded `Volume`
    row of their own."""

    def __init__(
        self, parent: wx.Window, repository: VolumeRepository, disk_usage_repository: DiskUsageRepository
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._disk_usage_repository = disk_usage_repository
        self._volumes: List[Volume] = []
        self._visible: List[Volume] = []
        # Names still awaiting a Calculate job result - tracked explicitly
        # rather than inferred from Volume.size_bytes being None, since
        # "not calculated yet" and "still calculating" both start that way.
        self._pending_names: Set[str] = set()
        self._calculating = False
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
        self._calculate_btn = wx.Button(self, label="Calculate")
        self._remove_btn = wx.Button(self, label="Remove")
        self._prune_btn = wx.Button(self, label="Prune unused")
        actions_bar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._info_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._calculate_btn, 0, wx.RIGHT, 8)
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

        self._include_named_checkbox = wx.CheckBox(self, label="Prune: include named unused volumes")
        filters_bar.Add(self._include_named_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

        outer.Add(filters_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)

        # No wx.LC_SINGLE_SEL - this list is deliberately multi-select (see
        # the class docstring), unlike every other list in this app.
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        # Sortable columns: repository.list() already returns volumes
        # sorted by name, so that's also the initial header sort state.
        self._sort_column = 0
        self._sort_ascending = True

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_name_filter_cancel)
        self._status_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._info_btn.Bind(wx.EVT_BUTTON, self._on_info)
        self._calculate_btn.Bind(wx.EVT_BUTTON, self._on_calculate)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._prune_btn.Bind(wx.EVT_BUTTON, self._on_prune)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        # Double-click (or Enter on a focused row) a volume to jump straight
        # to its details, same shortcut ContainersPage gives its own rows -
        # Info is still there on the toolbar for a single click.
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_info)
        # Delete key as a shortcut for Remove, covering the whole selection
        # same as clicking the button would.
        self._list.Bind(wx.EVT_LIST_KEY_DOWN, self._on_list_key_down)

        self._update_button_states(None)
        self._update_column_headers()
        self.reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._async.is_busy() or self._calculating:
            return
        self._set_loading(True)
        self._async.run(
            work=self._repository.list,
            on_success=self._on_loaded,
            on_error=self._on_load_error,
            on_done=lambda: self._set_loading(False),
            disable=[self._refresh_btn, self._calculate_btn],
        )

    def _set_loading(self, loading: bool) -> None:
        self._loading_text.SetLabel("Loading..." if loading else "")
        if loading and not self._volumes:
            self._show_loading_placeholder()

    def _show_loading_placeholder(self) -> None:
        self._list.DeleteAllItems()
        self._list.InsertItem(0, "Loading volumes...")

    def _on_loaded(self, volumes: List[Volume]) -> None:
        self._set_error(None)
        self._volumes = volumes
        # A fresh list means fresh Volume objects - any sizes computed by a
        # previous Calculate pass are gone with them, same "Refresh resets
        # every row back to Not calculated" behavior as Containers Disk.
        self._pending_names = set()
        self._populate_list()

    def _on_load_error(self, exc: Exception) -> None:
        self._set_error(str(exc))
        self._volumes = []
        self._pending_names = set()
        self._populate_list()

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    # ------------------------------------------------------------------
    # Calculate - the expensive part, only ever runs on button press
    # ------------------------------------------------------------------
    def _on_calculate(self, event: Optional[wx.CommandEvent]) -> None:
        if self._calculating or self._async.is_busy() or not self._volumes:
            return

        self._calculating = True
        # Sizes every currently-loaded volume, not just what the filters
        # happen to be showing right now - a filter changed after
        # Calculate starts shouldn't leave some rows permanently
        # uncalculated, and the "(n/total)" progress readout stays honest
        # against the real total this way.
        self._pending_names = {v.name for v in self._volumes}
        for volume in self._volumes:
            volume.size_bytes = None
            volume.size_error = None
        self._update_button_states(None)
        self._populate_list()
        self._report_progress()

        for volume in self._volumes:
            self._start_volume_job(volume)

    def _start_volume_job(self, volume: Volume) -> None:
        """Each volume is sized by its own independent job, same reasoning
        as ContainersDiskPage._start_container_job - whichever finishes
        first renders first, rather than every row waiting on the
        slowest. `MAX_CONCURRENT_DU_RUNS` still caps how many `du` helper
        containers run at once - and since VolumesPage and
        ContainersDiskPage are constructed against the very same
        DiskUsageRepository instance (see frame.py), that cap is shared
        across both screens, not doubled if a user runs Calculate on both
        at once."""
        name = volume.name

        def work():
            self._disk_usage_repository.ensure_helper_image()
            return self._disk_usage_repository.volume_usage_bytes(name)

        def success(size_bytes: int) -> None:
            volume.size_bytes = size_bytes
            self._pending_names.discard(name)
            self._report_progress()

        def error(exc: Exception) -> None:
            volume.size_error = str(exc)
            self._pending_names.discard(name)
            self._report_progress()

        run_background(work, on_success=success, on_error=error)

    def _report_progress(self) -> None:
        total = len(self._volumes)
        done = total - len(self._pending_names)
        if self._pending_names:
            self._loading_text.SetLabel(f"Calculating sizes... ({done}/{total})")
        else:
            self._calculating = False
            self._loading_text.SetLabel("")
            self._update_button_states(None)
        self._populate_list()

    # ------------------------------------------------------------------
    # Filtering / rendering
    # ------------------------------------------------------------------
    def _filtered_volumes(self) -> List[Volume]:
        name = self._name_filter.GetValue().strip().lower()
        status = self._status_choice.GetStringSelection()

        result = []
        for volume in self._volumes:
            if name and name not in volume.name.lower():
                continue
            if status != "All" and volume.status != status:
                continue
            result.append(volume)
        return result

    def _populate_list(self) -> None:
        # Preserve the whole selection, not just one row - a resort/filter/
        # refresh mid multi-select shouldn't collapse it down to one item.
        selected_names = {v.name for v in self._selected_volumes()}

        self._visible = self._filtered_volumes()
        self._sort_visible()
        self._list.DeleteAllItems()
        for row, volume in enumerate(self._visible):
            self._list.InsertItem(row, volume.name)
            self._list.SetItem(row, 1, volume.driver)
            self._list.SetItem(row, 2, volume.mountpoint)
            self._list.SetItem(row, 3, str(volume.containers))
            self._list.SetItem(row, 4, _joined(volume.container_names))
            self._list.SetItem(row, 5, _joined(volume.image_names))
            self._list.SetItem(row, 6, volume.status)
            self._list.SetItem(row, 7, volume_size_text(volume, self._pending_names))
            if volume.name in selected_names:
                self._list.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

        self._update_button_states(None)

    def _sort_visible(self) -> None:
        if self._sort_column == _SIZE_COLUMN:
            self._visible.sort(
                key=lambda v: _size_sort_key(v, self._pending_names), reverse=not self._sort_ascending
            )
        else:
            key_func = _SORT_KEYS[self._sort_column]
            self._visible.sort(key=key_func, reverse=not self._sort_ascending)

    def _on_col_click(self, event: wx.ListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            # Size defaults to "biggest first" - every other column reads
            # more naturally A-Z first, same reasoning as
            # ContainersDiskPage's Disk Usage column.
            self._sort_ascending = column != _SIZE_COLUMN
        self._update_column_headers()
        self._populate_list()

    def _update_column_headers(self) -> None:
        for index, label in enumerate(self._column_labels):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            column_info = self._list.GetColumn(index)
            column_info.SetText(label)
            self._list.SetColumn(index, column_info)

    def _selected_volume(self) -> Optional[Volume]:
        """The single selected volume - for actions (Info, the button form
        of Remove) that only make sense against exactly one row. Returns
        `None` for zero *or* more than one selected, unlike
        `_selected_volumes()` below."""
        volumes = self._selected_volumes()
        return volumes[0] if len(volumes) == 1 else None

    def _selected_volumes(self) -> List[Volume]:
        """Every currently selected volume, in list order - this is a
        multi-select list (see the class docstring), so callers that act on
        "the selection" (Remove) should use this, not `_selected_volume()`."""
        volumes = []
        index = self._list.GetFirstSelected()
        while index != -1:
            if index < len(self._visible):
                volumes.append(self._visible[index])
            index = self._list.GetNextSelected(index)
        return volumes

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        volumes = self._selected_volumes()
        # While a Calculate pass is running, Refresh/Remove/Prune are held
        # off rather than left to race a background `du` job that's
        # holding a reference to the very Volume objects Refresh would
        # replace, or a container that's mid-removal from under it.
        self._refresh_btn.Enable(not self._calculating)
        # Info only makes sense for exactly one volume at a time.
        self._info_btn.Enable(len(volumes) == 1)
        self._calculate_btn.Enable(not self._calculating and bool(self._volumes))
        self._remove_btn.Enable(bool(volumes) and not self._calculating)
        self._prune_btn.Enable(not self._calculating)

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
        # Enter on a row (wx.EVT_LIST_ITEM_ACTIVATED, a wx.ListEvent). The
        # activation event names the exact row that was double-clicked/
        # entered, which - unlike _selected_volume() - still resolves to
        # one volume even if a multi-select happens to include others.
        if isinstance(event, wx.ListEvent):
            index = event.GetIndex()
            volume = self._visible[index] if 0 <= index < len(self._visible) else None
        else:
            volume = self._selected_volume()
        if volume is None:
            return
        show_volume_details(
            self, volume.name, self._repository, self._disk_usage_repository, initial=volume
        )

    def _on_list_key_down(self, event: wx.ListEvent) -> None:
        if event.GetKeyCode() == wx.WXK_DELETE:
            self._on_remove(event)
        else:
            event.Skip()

    def _apply_removed(self, names: List[str]) -> None:
        """Mirrors ImagesPage._apply_removed - drop the given volumes from
        the already-loaded list and re-render immediately instead of
        waiting on a full `docker volume ls` round trip. Takes a list (not
        one name) so a multi-select removal renders as a single batch
        rather than one re-render per volume. Any size already calculated
        for the *other* rows is untouched, since this only filters the
        list rather than replacing it."""
        removed = set(names)
        self._volumes = [v for v in self._volumes if v.name not in removed]
        self._pending_names -= removed
        self._populate_list()

    def _on_remove(self, event: wx.Event) -> None:
        volumes = self._selected_volumes()
        if not volumes:
            return

        # Unlike containers/images there's no `-f` override for "volume is
        # in use" - docker refuses outright regardless, so this explains
        # why up front instead of firing calls that are guaranteed to fail
        # with docker's own less specific error. Removable volumes still go
        # ahead rather than the whole selection being blocked by the ones
        # that can't.
        removable = [v for v in volumes if not v.is_in_use]
        blocked = [v for v in volumes if v.is_in_use]

        if not blocked:
            prompt = (
                f'Remove volume "{removable[0].name}"?'
                if len(removable) == 1
                else f"Remove {len(removable)} volumes?"
            )
        elif not removable:
            noun = "Volume" if len(blocked) == 1 else "All selected volumes"
            verb = "is" if len(blocked) == 1 else "are"
            wx.MessageBox(
                f'{noun} {verb} in use and can\'t be removed: '
                f'{", ".join(v.name for v in blocked)}.\n\n'
                "Docker won't remove a volume that's in use - remove those "
                "containers first.",
                "Cannot remove",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        else:
            prompt = (
                f"Remove {len(removable)} of {len(volumes)} selected volumes?\n\n"
                f"{len(blocked)} will be skipped - still in use: "
                f'{", ".join(v.name for v in blocked)}.'
            )

        confirm = wx.MessageBox(prompt, "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        names = [v.name for v in removable]

        def work() -> List[Tuple[str, Optional[str]]]:
            # One `docker volume rm` per name, continuing past an
            # individual failure rather than aborting the whole batch over
            # one bad volume - same posture as ImageRepository's cascading
            # remove. DockerNotAvailableError is caught per-item too (rather
            # than left to abort the loop and hit the run()-level on_error
            # below) so one missing `docker` binary is reported the same
            # way for every name instead of just the first.
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
                    f"Could not remove {len(failed)} of {len(names)} volume(s):\n\n{details}",
                    "Remove failed",
                    wx.OK | wx.ICON_ERROR,
                    self,
                )

        def on_error(exc: Exception) -> None:
            # Only reachable for a truly unexpected failure - work() itself
            # catches both known docker exception types per-item above.
            wx.MessageBox(f"Could not remove volumes:\n\n{exc}", "Remove failed", wx.OK | wx.ICON_ERROR, self)

        self._async.run(
            work=work,
            on_success=on_success,
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn, self._calculate_btn],
        )

    def _on_prune(self, event: wx.CommandEvent) -> None:
        all_unused = self._include_named_checkbox.GetValue()
        prompt = (
            "Remove every unused volume - including named volumes not used "
            "by any container, not just anonymous ones?"
            if all_unused
            else "Remove every unused anonymous volume?"
        )
        confirm = wx.MessageBox(prompt, "Confirm prune", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        def on_success(summary: str) -> None:
            wx.MessageBox(summary.strip() or "Nothing to remove.", "Prune complete", wx.OK | wx.ICON_INFORMATION, self)
            # See ImagesPage._on_prune's comment on why this has to go
            # through wx.CallAfter rather than calling reload() directly.
            wx.CallAfter(self.reload)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Could not prune volumes:\n\n{exc}", "Prune failed", wx.OK | wx.ICON_ERROR, self)

        self._async.run(
            work=lambda: self._repository.prune(all_unused=all_unused),
            on_success=on_success,
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn, self._calculate_btn],
        )
