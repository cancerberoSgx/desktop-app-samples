import threading
from typing import Any, Callable, List, Optional

import wx

TaskWork = Callable[["TaskHandle"], Any]


class TaskHandle:
    """Cooperative cancellation handle for one in-flight background task,
    passed into the `work` callable given to `TaskManager.start()`. A loop
    over several steps (statements in a script, tables in an export) should
    poll `is_cancelled()` between iterations; a call blocked on a single
    slow driver operation can instead register a best-effort `interrupt`
    callable via `set_interrupt()` (e.g. a DuckDB connection's
    `.interrupt()`, a psycopg2 connection's `.cancel()`) which `cancel()`
    invokes immediately to abort whatever it's currently blocked on."""

    def __init__(self, description: str) -> None:
        self.description = description
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._interrupt: Optional[Callable[[], None]] = None

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def set_interrupt(self, fn: Optional[Callable[[], None]]) -> None:
        with self._lock:
            self._interrupt = fn

    def clear_interrupt(self) -> None:
        self.set_interrupt(None)

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._lock:
            fn = self._interrupt
        if fn is not None:
            try:
                fn()
            except Exception:
                pass  # best-effort - dropping the eventual result is enough either way


class TaskManager:
    """Runs at most one background task at a time, app-wide, so a slow
    Postgres connect/big CSV query/export never runs concurrently with
    another and stomps on it. Owned by MainFrame and threaded down into
    every page/dialog that kicks off long-running work; `TaskStatusBar` is
    the UI that visualizes it (gauge + click-for-details/cancel popup).

    `work(handle)` runs on a background thread and must not touch any wx
    widget. `on_success`/`on_error`/`on_cancelled` all run back on the UI
    thread (via `wx.CallAfter`) once the thread finishes.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._handle: Optional[TaskHandle] = None
        self._listeners: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def is_busy(self) -> bool:
        return self._handle is not None

    def current_description(self) -> Optional[str]:
        return self._handle.description if self._handle else None

    def add_listener(self, callback: Callable[[], None]) -> None:
        """`callback` is invoked (on the UI thread) whenever a task starts,
        finishes, or is cancelled - used by TaskStatusBar to show/hide/pulse
        its gauge."""
        self._listeners.append(callback)

    def _notify(self) -> None:
        for callback in self._listeners:
            callback()

    # ------------------------------------------------------------------
    # Starting / cancelling
    # ------------------------------------------------------------------
    def start(
        self,
        parent: wx.Window,
        description: str,
        work: TaskWork,
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Run `work` on a background thread. If another task is already
        running, asks the user (via a dialog parented on `parent`) whether
        to cancel it and run this one instead. Returns False if this task
        never started because the user declined that dialog."""
        if self.is_busy():
            current = self.current_description()
            dlg = wx.MessageDialog(
                parent,
                f'Currently running task "{current}".',
                "Task already running",
                wx.YES_NO | wx.ICON_WARNING,
            )
            dlg.SetYesNoLabels("Cancel current and run this one", "Cancel")
            try:
                choice = dlg.ShowModal()
            finally:
                dlg.Destroy()
            if choice != wx.ID_YES:
                return False
            self.cancel_current()

        handle = TaskHandle(description)
        self._handle = handle
        self._notify()

        def runner() -> None:
            try:
                result = work(handle)
            except Exception as exc:
                wx.CallAfter(self._finish, handle, None, exc, on_success, on_error, on_cancelled)
            else:
                wx.CallAfter(self._finish, handle, result, None, on_success, on_error, on_cancelled)

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        return True

    def cancel_current(self) -> None:
        """Best-effort cancel: marks the current task cancelled and
        immediately frees the slot for a new one, regardless of whether the
        abandoned background thread actually stops promptly - its result,
        whenever it eventually arrives, is dropped by `_finish` because the
        handle it was given is already marked cancelled."""
        if self._handle is not None:
            self._handle.cancel()
        self._handle = None
        self._thread = None
        self._notify()

    def _finish(
        self,
        handle: TaskHandle,
        result: Any,
        exc: Optional[Exception],
        on_success: Optional[Callable[[Any], None]],
        on_error: Optional[Callable[[Exception], None]],
        on_cancelled: Optional[Callable[[], None]],
    ) -> None:
        was_cancelled = handle.is_cancelled()
        if self._handle is handle:
            self._handle = None
            self._thread = None
            self._notify()
        if was_cancelled:
            if on_cancelled is not None:
                on_cancelled()
            return
        if exc is not None:
            if on_error is not None:
                on_error(exc)
            return
        if on_success is not None:
            on_success(result)
