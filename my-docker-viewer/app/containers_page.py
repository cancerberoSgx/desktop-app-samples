from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .formatting import size_sort_key
from .models import Container
from .repositories import ContainerRepository

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
    stop or remove the selected one. Auto-refresh is opt-in via a checkbox
    (off by default - the user must click Refresh otherwise) since CPU/memory
    are point-in-time samples that go stale after every load; when enabled it
    reloads on a timer (skipped while a request is already in flight)."""

    def __init__(self, parent: wx.Window, repository: ContainerRepository) -> None:
        super().__init__(parent)
        self._repository = repository
        self._containers: List[Container] = []
        self._visible: List[Container] = []
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Containers"), 0, wx.ALL, 12)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self._error_text.Hide()

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_filter = wx.SearchCtrl(self, size=(160, -1))
        self._name_filter.ShowCancelButton(True)
        toolbar.Add(self._name_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        toolbar.Add(wx.StaticText(self, label="Image:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._image_filter = wx.SearchCtrl(self, size=(160, -1))
        self._image_filter.ShowCancelButton(True)
        toolbar.Add(self._image_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        toolbar.Add(wx.StaticText(self, label="Status:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._status_choice = wx.Choice(self, choices=STATUS_CHOICES)
        self._status_choice.SetSelection(0)
        toolbar.Add(self._status_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        self._auto_refresh_checkbox = wx.CheckBox(
            self, label=f"Auto-refresh ({AUTO_REFRESH_INTERVAL_MS // 1000}s)"
        )
        self._auto_refresh_checkbox.SetValue(False)
        toolbar.Add(self._auto_refresh_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

        toolbar.AddStretchSpacer()

        self._loading_text = wx.StaticText(self, label="")
        self._loading_text.SetForegroundColour(wx.Colour(120, 120, 120))
        toolbar.Add(self._loading_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._refresh_btn = wx.Button(self, label="Refresh")
        self._stop_btn = wx.Button(self, label="Stop")
        self._remove_btn = wx.Button(self, label="Remove")
        toolbar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._stop_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._remove_btn, 0)

        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        # Sortable columns: repository.list() already returns containers
        # sorted by name, so that's also the initial header sort state.
        self._sort_column = 0
        self._sort_ascending = True

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_name_filter_cancel)
        self._image_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._image_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_image_filter_cancel)
        self._status_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._auto_refresh_checkbox.Bind(wx.EVT_CHECKBOX, self._on_auto_refresh_toggle)
        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)

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
        # The table only goes visibly blank on the very first load (or if
        # the last result was empty) - once rows are showing, a refresh
        # keeps them in place and this label is the only feedback, so an
        # in-flight auto-refresh tick doesn't blank a populated table.
        if loading and not self._containers:
            self._show_loading_placeholder()

    def _show_loading_placeholder(self) -> None:
        self._list.DeleteAllItems()
        self._list.InsertItem(0, "Loading containers...")

    def _on_loaded(self, containers: List[Container]) -> None:
        self._set_error(None)
        self._containers = containers
        self._populate_list()

    def _on_load_error(self, exc: Exception) -> None:
        self._set_error(str(exc))
        self._containers = []
        self._populate_list()

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
        selected = self._selected_container()
        selected_id = selected.id if selected else None

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
            if selected_id and container.id == selected_id:
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
        index = self._list.GetFirstSelected()
        if index == -1 or index >= len(self._visible):
            return None
        return self._visible[index]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        container = self._selected_container()
        self._stop_btn.Enable(container is not None and container.is_running)
        self._remove_btn.Enable(container is not None)

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

    def _apply_stopped(self, container_id: str) -> None:
        """Reflects a successful stop instantly, without waiting on a full
        `docker ps`/`docker stats` round trip: mutate the already-loaded
        container in place and re-render from it. A later manual or
        auto-refresh reconciles this with docker's actual reporting (exact
        status text, final size, ...) - this is just an optimistic, "we know
        it stopped" update."""
        for container in self._containers:
            if container.id == container_id:
                container.state = "exited"
                container.status = "Stopped"
                container.cpu_percent = None
                container.mem_usage = None
                container.mem_percent = None
                break
        self._populate_list()

    def _apply_removed(self, container_id: str) -> None:
        """Same idea as `_apply_stopped` - drop the container from the
        already-loaded list right away instead of re-fetching."""
        self._containers = [c for c in self._containers if c.id != container_id]
        self._populate_list()

    def _on_stop(self, event: wx.CommandEvent) -> None:
        container = self._selected_container()
        if container is None:
            return

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not stop "{container.names}":\n\n{exc}',
                "Stop failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.stop(container.id),
            on_success=lambda _result: self._apply_stopped(container.id),
            on_error=on_error,
            disable=[self._stop_btn, self._remove_btn],
        )

    def _on_remove(self, event: wx.CommandEvent) -> None:
        container = self._selected_container()
        if container is None:
            return

        force = container.is_running
        prompt = (
            f'Container "{container.names}" is currently running. Force remove it?'
            if force
            else f'Remove container "{container.names}"?'
        )
        confirm = wx.MessageBox(prompt, "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not remove "{container.names}":\n\n{exc}',
                "Remove failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.remove(container.id, force=force),
            on_success=lambda _result: self._apply_removed(container.id),
            on_error=on_error,
            disable=[self._stop_btn, self._remove_btn],
        )
