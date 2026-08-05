from typing import Any, Callable, Iterable, Optional

import wx
import wx.lib.delayedresult as delayedresult

"""Facade for running blocking calls (docker CLI invocations) on a
background thread and delivering their outcome back on the UI thread, so wx
never freezes while `docker ps`/`docker stats`/`docker stop`/`docker rm` are
in flight. Built on wx.lib.delayedresult (thread + wx.CallAfter under the
hood) - see ContainersPage.reload for the intended usage, and reuse the same
AsyncTaskRunner instance for any new blocking action a page adds.
"""


def run_background(
    work: Callable[[], Any],
    on_success: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> None:
    """Lower-level primitive behind AsyncTaskRunner.run(), for callers that
    need several independent, concurrently-streaming jobs rather than one
    task at a time - e.g. computing per-container disk usage, where each
    container's row should update as soon as THAT job finishes, regardless
    of how long the others take. AsyncTaskRunner's single-flight/is_busy/
    disable bookkeeping only makes sense for one task bound to specific
    widgets, so it doesn't fit that shape - this is just the
    delayedresult.startWorker + destroyed-window-safety plumbing on its own,
    callable as many times as needed. See ContainersDiskPage for the
    intended usage.
    """

    def _consumer(delayed_result: "delayedresult.DelayedResult") -> None:
        try:
            try:
                result = delayed_result.get()
            except Exception as exc:  # noqa: BLE001 - re-raised job exception
                if on_error:
                    on_error(exc)
            else:
                if on_success:
                    on_success(result)
        except RuntimeError:
            # The window any callback touches was destroyed while the job
            # was in flight - nothing left to update.
            pass

    delayedresult.startWorker(_consumer, work)


class AsyncTaskRunner:
    """Bound to a single window; runs one blocking callable at a time on a
    worker thread and calls back on the UI thread.

    One instance per page, reused for every blocking action that page
    offers:

        self._async = AsyncTaskRunner(self)
        ...
        self._async.run(
            work=lambda: self._repository.list(),
            on_success=self._populate,
            on_error=self._show_error,
            disable=[self._refresh_btn],
        )
    """

    def __init__(self, window: wx.Window) -> None:
        self._window = window
        self._busy = False

    def is_busy(self) -> bool:
        return self._busy

    def run(
        self,
        work: Callable[[], Any],
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_done: Optional[Callable[[], None]] = None,
        disable: Iterable[wx.Window] = (),
    ) -> None:
        """Run `work` (a zero-arg callable that may block, e.g. a docker CLI
        invocation) on a background thread.

        - on_success(result) fires if `work()` returned normally.
        - on_error(exc) fires if `work()` raised - the caller decides how to
          surface it (message box, status text, inline banner...).
        - on_done() always fires afterwards, success or failure - use it for
          cleanup that isn't covered by `disable`.
        - `disable` lists widgets (typically the triggering button) to
          disable while the task is running and re-enable once it's done,
          covering the common "don't let the user submit twice" case.

        A second call while one is already in flight on this runner is
        ignored, so a stray double-click (or an auto-refresh timer tick)
        can't stack overlapping docker CLI invocations.

        If the bound window is destroyed before the worker finishes (e.g.
        the user navigated away), callbacks are skipped instead of raising.
        """
        if self._busy:
            return
        self._busy = True

        widgets = list(disable)
        for widget in widgets:
            widget.Enable(False)

        def _consumer(delayed_result: "delayedresult.DelayedResult") -> None:
            try:
                try:
                    result = delayed_result.get()
                except Exception as exc:  # noqa: BLE001 - re-raised job exception
                    if on_error:
                        on_error(exc)
                else:
                    if on_success:
                        on_success(result)
                if on_done:
                    on_done()
                for widget in widgets:
                    widget.Enable(True)
            except RuntimeError:
                # The bound window (or one of `disable`'s widgets) was
                # destroyed while the job was in flight - nothing left to
                # update.
                pass
            finally:
                self._busy = False

        delayedresult.startWorker(_consumer, work)
