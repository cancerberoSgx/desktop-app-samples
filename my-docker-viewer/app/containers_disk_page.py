from typing import Dict, List, Optional, Set

import wx

from .async_task import AsyncTaskRunner, run_background
from .models import ContainerDiskUsage
from .repositories import DiskUsageRepository

# (header label, initial width) per list column - index-aligned to the
# columns as inserted into the wx.ListCtrl and to _SORT_KEYS below.
_COLUMNS = [
    ("Container ID", 110),
    ("Name", 180),
    ("Image", 220),
    ("Disk Usage", 120),
    ("Storage", 340),
]

_KIND_LABELS = {"volume": "volume", "bind": "bind mount", "tmpfs": "tmpfs"}


def _format_bytes(num_bytes: int) -> str:
    """Human-friendly decimal size (matches how most disk-usage tools read,
    including this app's existing docker-ps-derived Size column) - always
    computed from a raw byte count we control, never from `du`'s own
    heuristics, so formatting stays consistent across mounts."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000


def _disk_usage_text(container: ContainerDiskUsage, pending: Set[str]) -> str:
    if container.error is not None:
        return f"Error: {container.error}"
    if container.id in pending:
        return "Calculating..."
    total = container.total_bytes
    if total is None:
        return "Not calculated"
    return _format_bytes(total)


def _storage_summary(container: ContainerDiskUsage) -> str:
    if not container.mounts and not container.notes:
        return "Container layer only"

    counts: Dict[str, int] = {}
    shared = False
    for mount in container.mounts:
        counts[mount.kind] = counts.get(mount.kind, 0) + 1
        if mount.shared:
            shared = True

    parts = []
    for kind in ("volume", "bind", "tmpfs"):
        count = counts.pop(kind, 0)
        if count:
            label = _KIND_LABELS[kind]
            parts.append(f"{count} {label}{'s' if count != 1 else ''}")
    for kind, count in counts.items():  # anything else docker reports (npipe, ...)
        parts.append(f"{count} {kind}")

    summary = ", ".join(parts) if parts else "Container layer only"
    if shared:
        summary += " (shared)"
    if container.notes:
        summary += " - " + "; ".join(container.notes)
    return summary


def _disk_usage_sort_key(container: ContainerDiskUsage, pending: Set[str]):
    # Sorting must stay stable/sane regardless of state: errored or
    # still-calculating rows sort as lowest so a descending sort ("biggest
    # first", the whole point of this screen) surfaces real numbers first.
    if container.error is not None or container.id in pending:
        return -1
    total = container.total_bytes
    return -1 if total is None else total


class ContainersDiskPage(wx.Panel):
    """Read-only view of real per-container disk usage - the container's own
    writable layer plus every volume/bind mount it uses - to answer "which
    containers are using the most disk space" when you need to free some up.
    No stop/remove here; this screen only reads.

    Identity + mount composition load automatically (cheap - no `du`, no
    `--size`); actual sizing only ever runs via Calculate, since it means a
    filesystem walk per mount (see DiskUsageRepository) - either the user
    pressing the button, or once automatically the first time the user
    navigates to this page (see on_shown), so it isn't a wall of "Not
    calculated" the first time you'd have to already know to click past.
    Each container's total streams in independently as soon as ITS mounts
    finish sizing, rather than waiting for the slowest one - see
    _on_calculate."""

    def __init__(self, parent: wx.Window, repository: DiskUsageRepository) -> None:
        super().__init__(parent)
        self._repository = repository
        self._containers: List[ContainerDiskUsage] = []
        self._pending_ids: Set[str] = set()
        # Tracked explicitly rather than read back off AsyncTaskRunner.is_busy()
        # - that flag doesn't clear until after on_done runs, i.e. after the
        # exact point _update_button_states needs to read it from on_done.
        self._loading = False
        self._calculating = False
        # See on_shown()/_try_auto_calculate(): auto-runs Calculate the
        # first time (and only the first time) the user navigates to this
        # page, so it isn't a wall of "Not calculated" that requires
        # knowing to press a button first.
        self._visited = False
        self._auto_calculated = False
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Containers Disk"), 0, wx.ALL, 12)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self._error_text.Hide()

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._status_text = wx.StaticText(self, label="")
        self._status_text.SetForegroundColour(wx.Colour(120, 120, 120))
        toolbar.Add(self._status_text, 0, wx.ALIGN_CENTER_VERTICAL)

        toolbar.AddStretchSpacer()

        self._refresh_btn = wx.Button(self, label="Refresh")
        self._calculate_btn = wx.Button(self, label="Calculate")
        toolbar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._calculate_btn, 0)

        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        self._total_text = wx.StaticText(self, label="")
        outer.Add(self._total_text, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)

        self.SetSizer(outer)

        # Disk Usage is the one sort worth defaulting to on this screen -
        # "which container is biggest" is the whole point - descending so
        # the biggest offender is already on top.
        self._sort_column = 3
        self._sort_ascending = False

        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._calculate_btn.Bind(wx.EVT_BUTTON, self._on_calculate)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)

        self._update_column_headers()
        self._update_button_states()
        self.reload()

    # ------------------------------------------------------------------
    # Auto-Calculate on first visit only (see MainFrame._on_sidebar_select)
    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        """Called once per app run, the first time the user actually
        navigates to this page (not on construction - all pages are built
        eagerly at startup). Kicks off Calculate automatically that one
        time so the screen shows real numbers without requiring the user
        to already know to press the button; every visit after that -
        including after a Refresh, which resets every row back to "Not
        calculated" - is left entirely to the user, since Calculate is a
        comparatively expensive, deliberately opt-in action."""
        if self._visited:
            return
        self._visited = True
        self._try_auto_calculate()

    def _try_auto_calculate(self) -> None:
        if self._auto_calculated or self._calculating:
            return
        if self._loading:
            return  # _on_loaded calls this again once identity has landed
        if not self._containers:
            return
        self._auto_calculated = True
        self._on_calculate(None)

    # ------------------------------------------------------------------
    # Loading identity + mounts (cheap - independent of Calculate)
    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._loading or self._calculating:
            return
        self._loading = True
        self._set_status("Loading...")
        self._update_button_states()

        def on_done() -> None:
            self._loading = False
            self._set_status("")
            self._update_button_states()

        self._async.run(
            work=self._repository.list_targets,
            on_success=self._on_loaded,
            on_error=self._on_load_error,
            on_done=on_done,
            disable=[self._refresh_btn],
        )

    def _on_loaded(self, containers: List[ContainerDiskUsage]) -> None:
        self._set_error(None)
        self._containers = containers
        self._pending_ids = set()
        self._populate_list()
        self._update_total()
        self._try_auto_calculate()

    def _on_load_error(self, exc: Exception) -> None:
        self._set_error(str(exc))
        self._containers = []
        self._pending_ids = set()
        self._populate_list()
        self._update_total()

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    def _set_status(self, message: str) -> None:
        self._status_text.SetLabel(message)

    # ------------------------------------------------------------------
    # Calculate - the expensive part, only ever runs on button press
    # ------------------------------------------------------------------
    def _on_calculate(self, event: Optional[wx.CommandEvent]) -> None:
        if self._calculating or self._loading or not self._containers:
            return

        self._calculating = True
        self._pending_ids = {c.id for c in self._containers}
        for container in self._containers:
            container.layer_bytes = None
            container.mounts_bytes = None
            container.notes = []
            container.error = None
        self._total_text.SetLabel("")
        self._update_button_states()
        self._populate_list()
        self._report_progress()

        for container in self._containers:
            self._start_container_job(container)

    def _start_container_job(self, container: ContainerDiskUsage) -> None:
        """Everything for one container - its own writable-layer size AND
        its mounts total - runs as a single job, so this container's row is
        never held up by another container's pace. Earlier this fetched
        every container's layer size in one shared bulk call up front; that
        was measured to cost about the same *per container* as doing it
        here individually (~0.3-0.5s either way), so batching bought
        nothing but forced every row to wait on the slowest shared call -
        exactly the "faster ones should show first" streaming this screen
        is supposed to have."""
        container_id = container.id
        mounts = list(container.mounts)

        def work():
            # Idempotent and cheap once the helper image is cached (a
            # metadata check, no pull) - calling it from every job instead
            # of once up front keeps jobs fully independent; on a cold
            # cache several jobs may race to pull the same image, which
            # docker itself handles safely (layers dedupe at the daemon).
            self._repository.ensure_helper_image()
            layer_bytes_by_id = self._repository.container_layer_bytes([container_id])
            mounts_bytes, notes = self._repository.sum_mounts_bytes(mounts)
            return layer_bytes_by_id.get(container_id, 0), mounts_bytes, notes

        def success(result) -> None:
            layer_bytes, mounts_bytes, notes = result
            container.layer_bytes = layer_bytes
            container.mounts_bytes = mounts_bytes
            container.notes.extend(notes)
            self._pending_ids.discard(container.id)
            self._report_progress()

        def error(exc: Exception) -> None:
            container.error = str(exc)
            self._pending_ids.discard(container.id)
            self._report_progress()

        run_background(work, on_success=success, on_error=error)

    def _report_progress(self) -> None:
        total = len(self._containers)
        done = total - len(self._pending_ids)
        if self._pending_ids:
            self._set_status(f"Calculating... ({done}/{total})")
        else:
            self._calculating = False
            self._set_status("")
            self._update_button_states()
            self._update_total()
        self._populate_list()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _populate_list(self) -> None:
        rows = list(self._containers)
        key_func = _disk_usage_sort_key if self._sort_column == 3 else None
        if key_func is not None:
            rows.sort(key=lambda c: key_func(c, self._pending_ids), reverse=not self._sort_ascending)
        else:
            rows.sort(key=self._text_sort_key, reverse=not self._sort_ascending)

        self._list.DeleteAllItems()
        for row, container in enumerate(rows):
            self._list.InsertItem(row, container.id)
            self._list.SetItem(row, 1, container.names)
            self._list.SetItem(row, 2, container.image)
            self._list.SetItem(row, 3, _disk_usage_text(container, self._pending_ids))
            self._list.SetItem(row, 4, _storage_summary(container))

    def _text_sort_key(self, container: ContainerDiskUsage):
        return [container.id, container.names.lower(), container.image.lower(), "", _storage_summary(container).lower()][
            self._sort_column
        ]

    def _update_total(self) -> None:
        if not self._containers or any(c.total_bytes is None and c.error is None for c in self._containers):
            self._total_text.SetLabel("")
            return
        total = sum(c.total_bytes or 0 for c in self._containers)
        errored = sum(1 for c in self._containers if c.error is not None)
        message = f"Total: {_format_bytes(total)} across {len(self._containers)} container(s)"
        if errored:
            message += f" ({errored} could not be calculated)"
        self._total_text.SetLabel(message)

    def _on_col_click(self, event: wx.ListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            # Disk Usage defaults to "biggest first"; every other column
            # reads more naturally A-Z first.
            self._sort_ascending = column != 3
        self._update_column_headers()
        self._populate_list()

    def _update_column_headers(self) -> None:
        for index, label in enumerate(self._column_labels):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            column_info = self._list.GetColumn(index)
            column_info.SetText(label)
            self._list.SetColumn(index, column_info)

    def _update_button_states(self) -> None:
        busy = self._loading or self._calculating
        self._refresh_btn.Enable(not busy)
        self._calculate_btn.Enable(not busy and bool(self._containers))

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self.reload()
