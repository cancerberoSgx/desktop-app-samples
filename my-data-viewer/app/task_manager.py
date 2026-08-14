from enum import Enum
from typing import Any, Callable, List, Optional

import wx.lib.delayedresult as delayedresult

"""App-wide coordinator for long, deliberately-triggered, cancelable actions
- currently: exporting to Parquet (ActionsTab) and running a SQL script/
statement (ScriptsTab). One MainFrame-owned TaskManager backs the whole app,
so "only one task at a time, visible everywhere, cancelable from anywhere"
holds globally rather than per-page - unlike AsyncTaskRunner (app/
async_task.py), which is the right tool for routine per-page background
loads (list tables/columns, test_connection, ...) that don't need a visible
status or a cancel button.

Cancellation is best-effort, not a guarantee: cancel() flips this task to
CANCELING and invokes whatever `on_cancel_requested` was registered at
start() - typically a CancelToken's `.cancel()` (see drivers.py), which
fires every driver-level interrupt it collected (DuckDB/SQLite/Postgres all
abort an in-flight statement almost immediately this way). Whatever `work()`
does afterwards - raise, or return a partial/full result - is reported to
the caller as CANCELED rather than success or failure, since cancel() was
requested first; this keeps drivers from needing their own cancelled-vs-
finished signaling.
"""


class TaskStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELING = "canceling"


class TaskManager:
    """Bound to the app for its whole lifetime (MainFrame creates one).
    `subscribe` lets any widget - typically just the status bar - react to
    state changes; nothing else needs to poll it."""

    def __init__(self) -> None:
        self._status = TaskStatus.IDLE
        self._label: Optional[str] = None
        self._on_cancel_requested: Optional[Callable[[], None]] = None
        self._listeners: List[Callable[[TaskStatus, Optional[str]], None]] = []

    def subscribe(self, callback: Callable[[TaskStatus, Optional[str]], None]) -> None:
        """`callback(status, label)` fires on every state change - `label`
        is the running task's name while RUNNING/CANCELING, None once IDLE
        again."""
        self._listeners.append(callback)

    @property
    def status(self) -> TaskStatus:
        return self._status

    def is_busy(self) -> bool:
        return self._status != TaskStatus.IDLE

    def start(
        self,
        label: str,
        work: Callable[[], Any],
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
        on_cancel_requested: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Run `work` on a background thread if nothing else is running app-
        wide - returns False (and starts nothing) if a task is already in
        flight, so the caller can tell the user to wait or cancel it first.

        - on_success(result) / on_error(exc) fire if `work()` finished
          normally, UNLESS cancel() was called first - then on_cancelled()
          fires instead, regardless of what `work()` actually did.
        - `on_cancel_requested` is what cancel() invokes - pass a
          CancelToken's `.cancel()` so the in-flight driver call actually
          gets interrupted, not just abandoned.
        """
        if self.is_busy():
            return False
        self._status = TaskStatus.RUNNING
        self._label = label
        self._on_cancel_requested = on_cancel_requested
        self._notify()

        def _consumer(delayed_result: "delayedresult.DelayedResult") -> None:
            was_cancelling = self._status == TaskStatus.CANCELING
            try:
                try:
                    result = delayed_result.get()
                except Exception as exc:  # noqa: BLE001 - re-raised job exception
                    if was_cancelling:
                        if on_cancelled:
                            on_cancelled()
                    elif on_error:
                        on_error(exc)
                else:
                    if was_cancelling:
                        if on_cancelled:
                            on_cancelled()
                    elif on_success:
                        on_success(result)
            except RuntimeError:
                # A widget any callback touches was destroyed while the
                # task was in flight - nothing left to update.
                pass
            finally:
                self._status = TaskStatus.IDLE
                self._label = None
                self._on_cancel_requested = None
                self._notify()

        delayedresult.startWorker(_consumer, work)
        return True

    def cancel(self) -> None:
        if self._status != TaskStatus.RUNNING:
            return
        self._status = TaskStatus.CANCELING
        self._notify()
        if self._on_cancel_requested:
            self._on_cancel_requested()

    def _notify(self) -> None:
        for callback in self._listeners:
            callback(self._status, self._label)
