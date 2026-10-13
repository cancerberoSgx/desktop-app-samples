from typing import Callable, Dict, List, Optional, Tuple

import wx

from .async_task import AsyncTaskRunner, run_background
from .container_details_dialog import show_container_details
from .formatting import size_sort_key
from .models import Container
from .repositories import ContainerRepository, DiskUsageRepository, DockerCommandError, DockerNotAvailableError

STATUS_CHOICES = ["All", "running", "exited", "paused", "restarting", "created", "removing", "dead"]
AUTO_REFRESH_INTERVAL_MS = 5000


def _percent_sort_key(text: Optional[str]) -> float:
    if not text:
        return -1.0
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return -1.0


# (header label, initial width) per list column, and the matching sort-key
# function - both index-aligned to the columns as inserted into the wx.ListCtrl.
_COLUMNS = [
    ("Name", 160),
    ("Image", 200),
    ("Status", 170),
    ("Created", 190),
    ("CPU %", 70),
    ("Mem Usage", 130),
    ("Mem %", 70),
    ("Size", 150),
    ("Ports", 160),
    ("ID", 100),
]
_SORT_KEYS = [
    lambda c: c.names.lower(),
    lambda c: c.image.lower(),
    lambda c: c.status.lower(),
    # created_at is docker's raw timestamp ("2026-08-05 08:57:20 -0300 -03"),
    # not the friendlier created_for shown in the column - lexicographic order
    # on it matches chronological order.
    lambda c: c.created_at,
    lambda c: _percent_sort_key(c.cpu_percent),
    lambda c: size_sort_key(c.mem_usage),
    lambda c: _percent_sort_key(c.mem_percent),
    lambda c: size_sort_key(c.size),
    lambda c: c.ports.lower(),
    lambda c: c.id,
]


class ContainersPage(wx.Panel):
    """List every docker container (running and stopped), merged with live
    CPU/memory usage from `docker stats`; filter by name/image/status, and
    start, stop, or remove the selection. Auto-refresh is opt-in via a checkbox
    (off by default - the user must click Refresh otherwise) since CPU/memory
    are point-in-time samples that go stale after every load; when enabled it
    reloads on a timer (skipped while a request is already in flight).

    `reload()` fetches identity (`docker ps`) and live stats (`docker
    stats --no-stream`) as two independent, concurrent background jobs
    rather than one sequential call - `docker stats` has to wait out a full
    sampling window, and the table would otherwise sit blank that whole
    time even though the (near-instant) identity data was ready already.
    Identity renders the moment it lands, with cpu/mem showing "-"; stats
    fills those columns in whenever it finishes, in whichever order the two
    jobs happen to complete.

    Info opens `ContainerDetailsDialog` (`app/container_details_dialog.py`)
    for the selected container - identity, live cpu/mem, ports, networks,
    and real disk usage in one popup. That dialog is its own reusable
    component precisely so other screens can open the same view later from
    just a container id/name, without needing a loaded `Container` row of
    their own.

    The list is multi-select (`wx.LC_REPORT` without `wx.LC_SINGLE_SEL`),
    same as Images/Volumes - ctrl-click/shift-click are wx.ListCtrl's own
    native selection. Start/Stop/Remove (Delete key too) act on the whole
    selection: Start is enabled if any selected container isn't running and
    starts just those, Stop likewise for the running ones, and Remove takes
    every selected one (force-removing the running ones). Info needs
    exactly one row selected."""

    def __init__(
        self, parent: wx.Window, repository: ContainerRepository, disk_usage_repository: DiskUsageRepository
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._disk_usage_repository = disk_usage_repository
        self._containers: List[Container] = []
        self._visible: List[Container] = []
        self._async = AsyncTaskRunner(self)
        # reload() bookkeeping: two independent background jobs (identity +
        # stats) run concurrently per cycle - see reload()'s docstring.
        # _reload_pending counts how many of those two are still in flight
        # (0 = idle); _pending_stats holds whichever of the two lands first
        # until the other one is ready to be merged with it.
        self._reload_pending = 0
        self._identity_ready = False
        self._pending_stats: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {}

        outer = wx.BoxSizer(wx.VERTICAL)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self._error_text.Hide()

        # Row 1: actions - kept on its own row (rather than sharing one row
        # with the filters, as earlier versions did) so the buttons stay
        # reachable even when the window's too narrow to fit both - see
        # this page's docstring.
        actions_bar = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        self._info_btn = wx.Button(self, label="Info")
        self._start_btn = wx.Button(self, label="Start")
        self._stop_btn = wx.Button(self, label="Stop")
        self._remove_btn = wx.Button(self, label="Remove")
        actions_bar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._info_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._start_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._stop_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._remove_btn, 0)

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

        filters_bar.Add(wx.StaticText(self, label="Image:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._image_filter = wx.SearchCtrl(self, size=(160, -1))
        self._image_filter.ShowCancelButton(True)
        filters_bar.Add(self._image_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        filters_bar.Add(wx.StaticText(self, label="Status:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._status_choice = wx.Choice(self, choices=STATUS_CHOICES)
        self._status_choice.SetSelection(0)
        filters_bar.Add(self._status_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        self._auto_refresh_checkbox = wx.CheckBox(
            self, label=f"Auto-refresh ({AUTO_REFRESH_INTERVAL_MS // 1000}s)"
        )
        self._auto_refresh_checkbox.SetValue(False)
        filters_bar.Add(self._auto_refresh_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

        outer.Add(filters_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)

        # No wx.LC_SINGLE_SEL - multi-select (see the class docstring).
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        # Sortable columns: repository.list_identity() already returns
        # containers sorted by name, so that's also the initial header sort
        # state.
        self._sort_column = 0
        self._sort_ascending = True

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_name_filter_cancel)
        self._image_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._image_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_image_filter_cancel)
        self._status_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._auto_refresh_checkbox.Bind(wx.EVT_CHECKBOX, self._on_auto_refresh_toggle)
        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._info_btn.Bind(wx.EVT_BUTTON, self._on_info)
        self._start_btn.Bind(wx.EVT_BUTTON, self._on_start)
        self._stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        # Double-click (or Enter on a focused row) a container to jump
        # straight to its details, same shortcut a file manager gives you
        # for "open" - Info is still there on the toolbar for a single click.
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_info)
        # Delete key as a shortcut for Remove, covering the whole selection
        # same as clicking the button would.
        self._list.Bind(wx.EVT_LIST_KEY_DOWN, self._on_list_key_down)

        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        # Off by default - the user opts in via the checkbox above, since it
        # means an unattended, recurring docker CLI hit (docker ps + docker
        # stats) rather than one the user explicitly asked for.
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)

        self._update_button_states(None)
        self._update_column_headers()
        self.reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def reload(self) -> None:
        # Guard against both directions: don't stack a new reload cycle on
        # top of one still in flight (the timer, mainly), and don't run
        # while a start/stop/remove is in flight either - those mutate
        # self._containers in place, and a reload landing mid-mutation
        # would just clobber it (self-corrects next cycle, but no reason
        # to race).
        if self._async.is_busy() or self._reload_pending:
            return
        self._set_loading(True)
        self._refresh_btn.Enable(False)
        self._reload_pending = 2
        self._identity_ready = False
        self._pending_stats = {}
        run_background(
            work=self._repository.list_identity,
            on_success=self._on_identity_loaded,
            on_error=self._on_identity_error,
        )
        run_background(
            work=self._repository.stats,
            on_success=self._on_stats_loaded,
            on_error=self._on_stats_error,
        )

    def _set_loading(self, loading: bool) -> None:
        self._loading_text.SetLabel("Loading..." if loading else "")
        # The table only goes visibly blank on the very first load (or if
        # the last result was empty) - once rows are showing, a refresh
        # keeps them in place and this label is the only feedback, so an
        # in-flight auto-refresh tick doesn't blank a populated table.
        if loading and not self._containers:
            self._show_loading_placeholder()

    def _show_loading_placeholder(self) -> None:
        self._list.DeleteAllItems()
        self._list.InsertItem(0, "Loading containers...")

    def _on_identity_loaded(self, containers: List[Container]) -> None:
        self._set_error(None)
        # Stats can land before identity does (rare, but not impossible) -
        # apply whatever this cycle already has rather than showing a
        # moment of blank cpu/mem that a race just happened to avoid.
        self._apply_stats(containers, self._pending_stats)
        self._containers = containers
        self._identity_ready = True
        self._populate_list()
        self._finish_reload_step()

    def _on_identity_error(self, exc: Exception) -> None:
        self._set_error(str(exc))
        self._containers = []
        self._identity_ready = True
        self._populate_list()
        self._finish_reload_step()

    def _on_stats_loaded(self, stats_by_id: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]]) -> None:
        self._pending_stats = stats_by_id
        if self._identity_ready:
            self._apply_stats(self._containers, stats_by_id)
            self._populate_list()
        self._finish_reload_step()

    def _on_stats_error(self, exc: Exception) -> None:
        # `docker ps` succeeds or fails independently of `docker stats` -
        # don't blank out an otherwise-good table over a stats failure,
        # just leave cpu/mem showing "-" for this cycle.
        self._finish_reload_step()

    def _finish_reload_step(self) -> None:
        self._reload_pending = max(0, self._reload_pending - 1)
        if self._reload_pending == 0:
            self._set_loading(False)
            self._refresh_btn.Enable(True)

    @staticmethod
    def _apply_stats(
        containers: List[Container],
        stats_by_id: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]],
    ) -> None:
        for container in containers:
            stats = stats_by_id.get(container.id)
            if stats:
                container.cpu_percent, container.mem_usage, container.mem_percent = stats

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    def _on_timer(self, event: wx.TimerEvent) -> None:
        self.reload()

    def _on_auto_refresh_toggle(self, event: wx.CommandEvent) -> None:
        if self._auto_refresh_checkbox.GetValue():
            self._timer.Start(AUTO_REFRESH_INTERVAL_MS)
        else:
            self._timer.Stop()

    def _on_destroy(self, event: wx.WindowDestroyEvent) -> None:
        if event.GetEventObject() is self and self._timer.IsRunning():
            self._timer.Stop()
        event.Skip()

    # ------------------------------------------------------------------
    # Filtering / rendering
    # ------------------------------------------------------------------
    def _filtered_containers(self) -> List[Container]:
        name = self._name_filter.GetValue().strip().lower()
        image = self._image_filter.GetValue().strip().lower()
        status = self._status_choice.GetStringSelection()

        result = []
        for container in self._containers:
            if name and name not in container.names.lower():
                continue
            if image and image not in container.image.lower():
                continue
            if status != "All" and container.state.lower() != status.lower():
                continue
            result.append(container)
        return result

    def _populate_list(self) -> None:
        # Preserve the whole selection, not just one row - a resort/filter/
        # refresh mid multi-select shouldn't collapse it down to one item.
        selected_ids = {c.id for c in self._selected_containers()}

        self._visible = self._filtered_containers()
        self._sort_visible()
        self._list.DeleteAllItems()
        for row, container in enumerate(self._visible):
            self._list.InsertItem(row, container.names)
            self._list.SetItem(row, 1, container.image)
            self._list.SetItem(row, 2, container.status)
            self._list.SetItem(row, 3, container.created_for or container.created_at)
            self._list.SetItem(row, 4, container.cpu_percent or "-")
            self._list.SetItem(row, 5, container.mem_usage or "-")
            self._list.SetItem(row, 6, container.mem_percent or "-")
            self._list.SetItem(row, 7, container.size or "-")
            self._list.SetItem(row, 8, container.ports)
            self._list.SetItem(row, 9, container.id)
            if container.id in selected_ids:
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

    def _selected_container(self) -> Optional[Container]:
        """The single selected container - for Info, which only makes sense
        against exactly one row. Returns `None` for zero *or* more than one
        selected, unlike `_selected_containers()` below."""
        containers = self._selected_containers()
        return containers[0] if len(containers) == 1 else None

    def _selected_containers(self) -> List[Container]:
        """Every currently selected container, in list order - this is a
        multi-select list (see the class docstring), so Start/Stop/Remove
        act on this, not `_selected_container()`."""
        containers = []
        index = self._list.GetFirstSelected()
        while index != -1:
            if index < len(self._visible):
                containers.append(self._visible[index])
            index = self._list.GetNextSelected(index)
        return containers

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        containers = self._selected_containers()
        # Start/Stop are enabled as long as *some* selected container can
        # take the action - a mixed selection acts on the ones it applies
        # to and leaves the rest alone (see _on_start/_on_stop).
        self._info_btn.Enable(len(containers) == 1)
        self._start_btn.Enable(any(not c.is_running for c in containers))
        self._stop_btn.Enable(any(c.is_running for c in containers))
        self._remove_btn.Enable(bool(containers))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self._populate_list()

    def _on_name_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._name_filter.SetValue("")
        self._populate_list()

    def _on_image_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._image_filter.SetValue("")
        self._populate_list()

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self.reload()

    def _apply_started(self, container_ids: List[str]) -> None:
        """Mirrors `_apply_stopped`: reflect a successful start instantly
        rather than waiting on a full `docker ps`/`docker stats` round trip.
        cpu/mem are left `None` (not immediately re-fetched) since a
        container that just started has no `docker stats` sample yet
        either way - the next manual or auto-refresh picks those up along
        with docker's exact `Status` text ("Up 1 second", ...). Takes a
        list so a multi-select start renders as one batch."""
        started = set(container_ids)
        for container in self._containers:
            if container.id in started:
                container.state = "running"
                container.status = "Up"
        self._populate_list()

    def _apply_stopped(self, container_ids: List[str]) -> None:
        """Reflects a successful stop instantly, without waiting on a full
        `docker ps`/`docker stats` round trip: mutate the already-loaded
        containers in place and re-render from them. A later manual or
        auto-refresh reconciles this with docker's actual reporting (exact
        status text, final size, ...) - this is just an optimistic, "we know
        it stopped" update."""
        stopped = set(container_ids)
        for container in self._containers:
            if container.id in stopped:
                container.state = "exited"
                container.status = "Stopped"
                container.cpu_percent = None
                container.mem_usage = None
                container.mem_percent = None
        self._populate_list()

    def _apply_removed(self, container_ids: List[str]) -> None:
        """Same idea as `_apply_stopped` - drop the containers from the
        already-loaded list right away instead of re-fetching."""
        removed = set(container_ids)
        self._containers = [c for c in self._containers if c.id not in removed]
        self._populate_list()

    def _on_info(self, event: wx.Event) -> None:
        # Bound to both the Info button (wx.CommandEvent) and double-click/
        # Enter on a row (wx.EVT_LIST_ITEM_ACTIVATED, a wx.ListEvent). The
        # activation event names the exact row that was double-clicked/
        # entered, which - unlike _selected_container() - still resolves to
        # one container even if a multi-select happens to include others.
        if isinstance(event, wx.ListEvent):
            index = event.GetIndex()
            container = self._visible[index] if 0 <= index < len(self._visible) else None
        else:
            container = self._selected_container()
        if container is None:
            return
        show_container_details(
            self, container.id, self._repository, self._disk_usage_repository, initial=container
        )

    def _on_list_key_down(self, event: wx.ListEvent) -> None:
        if event.GetKeyCode() == wx.WXK_DELETE:
            self._on_remove(event)
        else:
            event.Skip()

    def _run_batch(
        self,
        containers: List[Container],
        action: Callable[[Container], None],
        verb: str,
        on_applied: Callable[[List[str]], None],
    ) -> None:
        """Run `action` against every container in `containers` as one
        `AsyncTaskRunner` task - one docker call per container, continuing
        past an individual failure rather than aborting the whole batch,
        same posture as VolumesPage's multi-select remove. Successes are
        applied optimistically (`on_applied`) in one re-render; failures
        are reported together in one message box afterwards."""

        def work() -> List[Tuple[Container, Optional[str]]]:
            # DockerNotAvailableError is caught per-item too, so a missing
            # `docker` binary is reported the same way for every container
            # instead of only aborting on the first.
            results = []
            for container in containers:
                try:
                    action(container)
                    results.append((container, None))
                except (DockerCommandError, DockerNotAvailableError) as exc:
                    results.append((container, str(exc)))
            return results

        def on_success(results: List[Tuple[Container, Optional[str]]]) -> None:
            succeeded = [c.id for c, error in results if error is None]
            failed = [(c, error) for c, error in results if error is not None]
            if succeeded:
                on_applied(succeeded)
            if failed:
                if len(results) == 1:
                    message = f'Could not {verb} "{failed[0][0].names}":\n\n{failed[0][1]}'
                else:
                    details = "\n".join(f'"{c.names}": {error}' for c, error in failed)
                    message = f"Could not {verb} {len(failed)} of {len(results)} container(s):\n\n{details}"
                wx.MessageBox(message, f"{verb.capitalize()} failed", wx.OK | wx.ICON_ERROR, self)

        def on_error(exc: Exception) -> None:
            # Only reachable for a truly unexpected failure - work() itself
            # catches both known docker exception types per-item above.
            wx.MessageBox(
                f"Could not {verb} containers:\n\n{exc}", f"{verb.capitalize()} failed", wx.OK | wx.ICON_ERROR, self
            )

        self._async.run(
            work=work,
            on_success=on_success,
            on_error=on_error,
            # AsyncTaskRunner re-enables everything in `disable` after
            # on_done, regardless of the new selection's state - recompute
            # button states once it has, rather than leaving e.g. Start
            # enabled on a selection that's now entirely running.
            on_done=lambda: wx.CallAfter(self._update_button_states, None),
            disable=[self._start_btn, self._stop_btn, self._remove_btn],
        )

    def _on_start(self, event: wx.CommandEvent) -> None:
        # reload() no longer runs on the shared AsyncTaskRunner (see its
        # docstring), so it can't rely on that runner's busy flag to keep
        # this from racing self._containers - check the reload counter
        # directly instead, same "just don't" as the busy check used to be.
        if self._reload_pending:
            return
        # A mixed selection starts only the ones not already running -
        # the already-running ones are simply left alone.
        targets = [c for c in self._selected_containers() if not c.is_running]
        if not targets:
            return
        self._run_batch(targets, lambda c: self._repository.start(c.id), "start", self._apply_started)

    def _on_stop(self, event: wx.CommandEvent) -> None:
        if self._reload_pending:
            return
        # Mirror of _on_start: only the running ones in the selection.
        targets = [c for c in self._selected_containers() if c.is_running]
        if not targets:
            return
        self._run_batch(targets, lambda c: self._repository.stop(c.id), "stop", self._apply_stopped)

    def _on_remove(self, event: wx.Event) -> None:
        containers = self._selected_containers()
        if not containers or self._reload_pending or self._async.is_busy():
            return

        running = [c for c in containers if c.is_running]
        if len(containers) == 1:
            container = containers[0]
            prompt = (
                f'Container "{container.names}" is currently running. Force remove it?'
                if running
                else f'Remove container "{container.names}"?'
            )
        else:
            prompt = f"Remove {len(containers)} containers?"
            if running:
                prompt += (
                    f"\n\n{len(running)} of them are currently running and will be force removed: "
                    f'{", ".join(c.names for c in running)}.'
                )
        confirm = wx.MessageBox(prompt, "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        self._run_batch(
            containers,
            # Force only the running ones - same as the single-container
            # behavior this replaced.
            lambda c: self._repository.remove(c.id, force=c.is_running),
            "remove",
            self._apply_removed,
        )
