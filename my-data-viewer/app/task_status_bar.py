import wx

from .async_tasks import TaskManager

_GAUGE_FIELD_WIDTH = 140
_PULSE_INTERVAL_MS = 100


class TaskPopup(wx.PopupTransientWindow):
    """Shown when the status bar's gauge is clicked while a task is
    running: the task's description plus a button to cancel it."""

    def __init__(self, parent: wx.Window, task_manager: TaskManager) -> None:
        super().__init__(parent, flags=wx.BORDER_SIMPLE)
        self._task_manager = task_manager

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        sizer = wx.BoxSizer(wx.VERTICAL)

        description = task_manager.current_description() or "Running..."
        label = wx.StaticText(panel, label=description)
        label.Wrap(240)
        sizer.Add(label, 0, wx.ALL, 12)

        cancel_btn = wx.Button(panel, label="Cancel")
        sizer.Add(cancel_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)

        panel.SetSizer(sizer)
        panel.Fit()
        self.SetClientSize(panel.GetSize())

    def _on_cancel(self, event: wx.CommandEvent) -> None:
        self._task_manager.cancel_current()
        self.Dismiss()


class TaskStatusBar(wx.StatusBar):
    """Extends the frame's status bar with a small progress gauge in the
    bottom-right, reflecting TaskManager's single in-flight task: pulses
    while busy, hidden while idle. Clicking it while busy shows the task's
    description and a "Cancel" option (`TaskPopup`)."""

    def __init__(self, parent: wx.Window, task_manager: TaskManager) -> None:
        super().__init__(parent)
        self._task_manager = task_manager
        self.SetFieldsCount(2)
        self.SetStatusStyles([wx.SB_NORMAL, wx.SB_FLAT])
        self.SetStatusWidths([-1, _GAUGE_FIELD_WIDTH])

        self._gauge = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        self._gauge.Hide()
        self._timer = wx.Timer(self)

        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self._gauge.Bind(wx.EVT_LEFT_UP, self._on_gauge_clicked)

        task_manager.add_listener(self._on_task_changed)
        self._reposition_gauge()

    def _on_size(self, event: wx.SizeEvent) -> None:
        self._reposition_gauge()
        event.Skip()

    def _reposition_gauge(self) -> None:
        rect = self.GetFieldRect(1)
        gauge_height = self._gauge.GetBestSize().height
        self._gauge.SetSize(
            rect.x + 4, rect.y + (rect.height - gauge_height) // 2, max(0, rect.width - 8), gauge_height
        )

    def _on_task_changed(self) -> None:
        if self._task_manager.is_busy():
            self._gauge.Show()
            self._gauge.SetToolTip(self._task_manager.current_description() or "")
            if not self._timer.IsRunning():
                self._timer.Start(_PULSE_INTERVAL_MS)
        else:
            self._timer.Stop()
            self._gauge.Hide()
        self._reposition_gauge()

    def _on_timer(self, event: wx.TimerEvent) -> None:
        self._gauge.Pulse()

    def _on_gauge_clicked(self, event: wx.MouseEvent) -> None:
        if not self._task_manager.is_busy():
            return
        popup = TaskPopup(self, self._task_manager)
        popup.Position(self._gauge.ClientToScreen((0, self._gauge.GetSize().height)), (0, 0))
        popup.Popup()
