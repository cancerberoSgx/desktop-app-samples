from typing import Any, Callable, Iterable, Optional

import wx
import wx.lib.delayedresult as delayedresult

"""Facade for running blocking calls (`du` invocations, filesystem walks) on
a background thread and delivering their outcome back on the UI thread, so
wx never freezes while a folder's disk usage is being scanned. Built on
wx.lib.delayedresult (thread + wx.CallAfter under the hood) - copied
verbatim from my-docker-viewer's app/async_task.py, which this project was
templated from for its async execution pattern (see ContainersDiskPage's
Calculate for the intended per-item-streaming usage this facade was built
for). Reuse the same AsyncTaskRunner instance for any new blocking action a
page adds.
"""


def run_background(
    work: Callable[[], Any],
    on_success: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> None:
    """Lower-level primitive behind AsyncTaskRunner.run(), for callers that
    need several independent, concurrently-streaming jobs rather than one
    task at a time - e.g. scanning a folder's immediate subdirectories,
    where each subdirectory's row should update as soon as THAT `du` call
    finishes, regardless of how long the others take. AsyncTaskRunner's
    single-flight/is_busy/disable bookkeeping only makes sense for one task
    bound to specific widgets, so it doesn't fit that shape - this is just
    the delayedresult.startWorker + destroyed-window-safety plumbing on its
    own, callable as many times as needed. See ExplorerPage (a later step)
    for the intended usage - one job per immediate subdirectory.
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
            work=lambda: self._cache.list_children(self._current_path),
            on_success=self._populate,
            on_error=self._show_error,
            disable=[self._reload_btn],
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
        """Run `work` (a zero-arg callable that may block, e.g. reading the
        SQLite cache or running `du`) on a background thread.

        - on_success(result) fires if `work()` returned normally.
        - on_error(exc) fires if `work()` raised - the caller decides how to
          surface it (message box, status text, inline banner...).
        - on_done() always fires afterwards, success or failure - use it for
          cleanup that isn't covered by `disable`.
        - `disable` lists widgets (typically the triggering button) to
          disable while the task is running and re-enable once it's done,
          covering the common "don't let the user submit twice" case.

        A second call while one is already in flight on this runner is
        ignored, so a stray double-click can't stack overlapping scans.

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
