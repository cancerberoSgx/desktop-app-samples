from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .models import Container
from .repositories import ContainerRepository

STATUS_CHOICES = ["All", "running", "exited", "paused", "restarting", "created", "removing", "dead"]
AUTO_REFRESH_INTERVAL_MS = 5000


class ContainersPage(wx.Panel):
    """List every docker container (running and stopped), merged with live
    CPU/memory usage from `docker stats`; filter by name/image/status, and
    stop or remove the selected one. Refreshes automatically every few
    seconds (skipped while a request is already in flight) since CPU/memory
    are point-in-time samples."""

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
        toolbar.Add(self._status_choice, 0, wx.ALIGN_CENTER_VERTICAL)

        toolbar.AddStretchSpacer()

        self._refresh_btn = wx.Button(self, label="Refresh")
        self._stop_btn = wx.Button(self, label="Stop")
        self._remove_btn = wx.Button(self, label="Remove")
        toolbar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._stop_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._remove_btn, 0)

        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        for index, (label, width) in enumerate(
            [
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
        ):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_name_filter_cancel)
        self._image_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._image_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_image_filter_cancel)
        self._status_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)

        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self._timer.Start(AUTO_REFRESH_INTERVAL_MS)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)

        self._update_button_states(None)
        self.reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._async.is_busy():
            return
        self._async.run(
            work=self._repository.list,
            on_success=self._on_loaded,
            on_error=self._on_load_error,
            disable=[self._refresh_btn],
        )

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
        self._list.DeleteAllItems()
        for row, container in enumerate(self._visible):
            self._list.InsertItem(row, container.names)
            self._list.SetItem(row, 1, container.image)
            self._list.SetItem(row, 2, container.status)
            self._list.SetItem(row, 3, container.created_at)
            self._list.SetItem(row, 4, container.cpu_percent or "-")
            self._list.SetItem(row, 5, container.mem_usage or "-")
            self._list.SetItem(row, 6, container.mem_percent or "-")
            self._list.SetItem(row, 7, container.size or "-")
            self._list.SetItem(row, 8, container.ports)
            self._list.SetItem(row, 9, container.id)
            if selected_id and container.id == selected_id:
                self._list.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

        self._update_button_states(None)

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
            on_success=lambda _result: self.reload(),
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
            on_success=lambda _result: self.reload(),
            on_error=on_error,
            disable=[self._stop_btn, self._remove_btn],
        )
