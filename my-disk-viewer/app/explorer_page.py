import os
import subprocess
import sys
from typing import Callable, List, Optional, Set, Tuple

import wx

from .async_task import run_background
from .cache_repository import CacheRepository
from .disk_scan_repository import DiskScanRepository
from .formatting import format_bytes
from .models import Entry, ImmediateListing, SubtreeScan
from .pie_chart import PieChartPanel

"""The one main screen of this app: a breadcrumb + drill-down table of
whatever folder is currently open. No sidebar/wx.Simplebook, unlike
my-docker-viewer's five resource-type screens - there's only one concept
here.

Uses `run_background` for BOTH the cheap "list this folder's immediate
children" load and the expensive Reload scan, not `AsyncTaskRunner` -
`AsyncTaskRunner`'s single-flight/disable bookkeeping is built for one task
bound to specific widgets, but Reload is inherently the "N independent
per-subdirectory jobs streaming back concurrently" shape (like
`ContainersDiskPage`'s Calculate), and the plain load fits the same
`run_background` + manually-tracked `self._loading`/`self._reloading` flags
better than introducing a second busy-tracking mechanism just for it.
`AsyncTaskRunner` stays available in `app/async_task.py` for a future
dialog that IS bound to specific widgets.

CRITICAL invariant for thread-safety: every `CacheRepository` call in this
module happens inside a `success`/`error` callback, NEVER inside a `work()`
callable passed to `run_background`. `work()` runs on a background thread;
`success`/`error` are guaranteed to run back on the main GUI thread (see
async_task.py's docstring - wx.lib.delayedresult uses wx.CallAfter under
the hood). sqlite3 connections aren't safe to use from multiple threads,
and Reload can have several subdirectory jobs in flight at once - keeping
every cache read/write inside the callbacks (which wx's event loop
processes one at a time) is what makes that safe without a lock.
"""

# Column indices, index-aligned to the columns as inserted and to
# _SORT_KEYS below.
_COL_NAME, _COL_TYPE, _COL_SIZE, _COL_PERCENT, _COL_ITEMS = range(5)
_COLUMNS = [
    ("Name", 340),
    ("Type", 70),
    ("Size", 110),
    ("% of Folder", 100),
    ("Items", 70),
]

# Caps how many `du` subprocesses a single Reload runs at once, regardless
# of how many immediate subdirectories the folder has - unbounded
# concurrency here would mean a folder with hundreds of subdirectories
# spawning hundreds of simultaneous `du` walks, which thrashes I/O rather
# than finishing any faster. Mirrors DiskUsageRepository's
# MAX_CONCURRENT_DU_RUNS semaphore in my-docker-viewer, just implemented as
# a plain queue here since there's no shared helper-container plumbing to
# gate.
MAX_CONCURRENT_SCAN_JOBS = 4


class ExplorerPage(wx.Panel):
    """Breadcrumb + drill-down table for the folder currently open.

    Opening a folder shows whatever's already cached instantly, then
    refines it once a cheap `os.scandir` listing lands (new/removed
    children). Reload is the only thing that actually runs `du` - once per
    immediate subdirectory, each its own independent background job so the
    fastest subfolder's row updates first - and is always manual, never
    automatic, since even the cheapest folder-tree scan is comparably
    expensive to everything else this page does; see CLAUDE.md."""

    def __init__(self, parent: wx.Window, scanner: DiskScanRepository, cache: CacheRepository) -> None:
        super().__init__(parent)
        self._scanner = scanner
        self._cache = cache

        self._current_path: Optional[str] = None
        self._folder_entry: Optional[Entry] = None
        self._entries: List[Entry] = []
        self._visible: List[Entry] = []
        self._pending_children: Set[str] = set()
        self._scan_queue: List[str] = []
        self._reload_total = 0
        self._skipped_total = 0
        self._loading = False
        self._reloading = False

        self._sort_column = _COL_SIZE
        self._sort_ascending = False

        # "children": the same items the table shows (this folder's
        # immediate subfolders/files) - the direct answer to "which
        # subfolder/file is using the most space". "extension": every
        # file recursively under this folder grouped by type - "which
        # file TYPE is using the most space", the other UX-agreed
        # breakdown. Both read from data already being kept in sync with
        # the table (see _update_chart, called from _populate_list), so
        # the chart is never a stale second copy of the same numbers.
        self._chart_mode = "children"

        self._build_ui()
        self._update_header()
        self._update_button_states()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._open_btn = wx.Button(self, label="Open Folder...")
        self._up_btn = wx.Button(self, label="Up")
        self._reload_btn = wx.Button(self, label="Reload")
        self._reveal_btn = wx.Button(self, label="Reveal in File Manager")
        toolbar.Add(self._open_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._up_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._reload_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._reveal_btn, 0)
        toolbar.AddStretchSpacer()
        self._status_text = wx.StaticText(self, label="")
        self._status_text.SetForegroundColour(wx.Colour(120, 120, 120))
        toolbar.Add(self._status_text, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 12)

        self._breadcrumb_panel = wx.Panel(self)
        self._breadcrumb_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._breadcrumb_panel.SetSizer(self._breadcrumb_sizer)
        outer.Add(self._breadcrumb_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self._error_text.Hide()

        self._header_text = wx.StaticText(self, label="")
        outer.Add(self._header_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._notebook = wx.Notebook(self)
        outer.Add(self._notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        table_page = wx.Panel(self._notebook)
        table_sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = wx.ListCtrl(table_page, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        table_sizer.Add(self._list, 1, wx.EXPAND)
        table_page.SetSizer(table_sizer)
        self._notebook.AddPage(table_page, "Table")

        chart_page = wx.Panel(self._notebook)
        chart_sizer = wx.BoxSizer(wx.VERTICAL)
        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        self._mode_children_radio = wx.RadioButton(chart_page, label="By subfolder/file", style=wx.RB_GROUP)
        self._mode_extension_radio = wx.RadioButton(chart_page, label="By file type")
        mode_row.Add(self._mode_children_radio, 0, wx.RIGHT, 16)
        mode_row.Add(self._mode_extension_radio, 0)
        chart_sizer.Add(mode_row, 0, wx.ALL, 8)
        self._chart_panel = PieChartPanel(chart_page)
        chart_sizer.Add(self._chart_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        chart_page.SetSizer(chart_sizer)
        self._notebook.AddPage(chart_page, "Chart")

        self.SetSizer(outer)
        self._update_column_headers()
        self._rebuild_breadcrumb()

        self._open_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        self._up_btn.Bind(wx.EVT_BUTTON, self._on_up)
        self._reload_btn.Bind(wx.EVT_BUTTON, self._on_reload)
        self._reveal_btn.Bind(wx.EVT_BUTTON, self._on_reveal)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        # Double-click (or Enter on a focused row) descends into a folder;
        # for a file it's the same as pressing Reveal - there's nothing
        # else "activating" a file row could mean on a read-only screen.
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_activate)
        self._mode_children_radio.Bind(wx.EVT_RADIOBUTTON, self._on_chart_mode_changed)
        self._mode_extension_radio.Bind(wx.EVT_RADIOBUTTON, self._on_chart_mode_changed)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def open_folder(self, path: str) -> None:
        """Opens `path` in the explorer - shows whatever's already cached
        for it instantly, then kicks off a cheap listing refresh (never
        `du` - that only ever runs via Reload)."""
        if self._loading or self._reloading:
            return
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            wx.MessageBox(f"'{path}' is not a folder.", "My Disk Viewer", wx.OK | wx.ICON_ERROR, self)
            return

        self._current_path = path
        self._pending_children = set()
        self._skipped_total = 0
        self._set_error(None)
        # Instant feel: whatever a previous session/Reload already cached
        # for this folder shows immediately, refined below once a fresh
        # os.scandir listing lands (new/removed children since then).
        self._folder_entry = self._cache.get_entry(path)
        self._entries = self._cache.list_children(path)
        self._rebuild_breadcrumb()
        self._update_header()
        self._populate_list()
        self._load_current_folder()

    def _on_up(self, event: wx.CommandEvent) -> None:
        if self._current_path is None:
            return
        self.open_folder(_parent_of(self._current_path))

    def _on_open_folder(self, event: wx.CommandEvent) -> None:
        with wx.DirDialog(
            self, "Choose a folder to analyze", style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.open_folder(dlg.GetPath())

    def _on_activate(self, event: wx.ListEvent) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.is_dir:
            self.open_folder(entry.path)
        else:
            _reveal_in_file_manager(entry.path)

    def _on_reveal(self, event: wx.CommandEvent) -> None:
        entry = self._selected_entry()
        if entry is not None:
            _reveal_in_file_manager(entry.path)

    # ------------------------------------------------------------------
    # Loading identity (cheap - no `du`, independent of Reload)
    # ------------------------------------------------------------------
    def _load_current_folder(self) -> None:
        path = self._current_path
        self._loading = True
        self._set_status("Loading...")
        self._update_button_states()

        def work() -> ImmediateListing:
            return self._scanner.list_immediate(path)

        def success(listing: ImmediateListing) -> None:
            self._loading = False
            self._set_status("")
            self._on_children_loaded(path, listing)
            self._update_button_states()

        def error(exc: Exception) -> None:
            self._loading = False
            self._set_status("")
            self._set_error(str(exc))
            self._update_button_states()

        run_background(work, on_success=success, on_error=error)

    def _on_children_loaded(self, path: str, listing: ImmediateListing) -> None:
        if path != self._current_path:
            return  # the user navigated elsewhere before this landed
        if listing.error is not None:
            self._set_error(listing.error)
            return
        self._set_error(None)
        existing_paths = set(listing.subdirs) | {f.path for f in listing.files}
        self._cache.prune_missing_children(path, existing_paths)
        self._cache.replace_files(path, listing.files)
        self._skipped_total = listing.skipped
        self._entries = self._merge_with_cache(path, listing)
        self._update_header()
        self._populate_list()

    def _merge_with_cache(self, path: str, listing: ImmediateListing) -> List[Entry]:
        """Every entry the user should see right now: files are always
        fully known already (just replaced into the cache above); folders
        get their cached total if one exists (a prior scan), or an
        unscanned placeholder Entry if this is the first time this
        subdirectory has ever been seen - so a brand-new subfolder shows
        up as "Not scanned" immediately rather than being invisible until
        the next Reload."""
        cached = {e.path: e for e in self._cache.list_children(path)}
        entries: List[Entry] = []
        for f in listing.files:
            entries.append(cached.get(f.path) or Entry(path=f.path, name=os.path.basename(f.path), is_dir=False, size_bytes=f.size_bytes, item_count=1))
        for subdir in listing.subdirs:
            entries.append(cached.get(subdir) or Entry(path=subdir, name=os.path.basename(subdir), is_dir=True))
        return entries

    # ------------------------------------------------------------------
    # Reload - the expensive part, only ever runs on button press
    # ------------------------------------------------------------------
    def _on_reload(self, event: Optional[wx.CommandEvent] = None) -> None:
        if self._current_path is None or self._loading or self._reloading:
            return
        path = self._current_path
        self._reloading = True
        self._set_status("Scanning...")
        self._update_button_states()

        def work() -> ImmediateListing:
            return self._scanner.list_immediate(path)

        def success(listing: ImmediateListing) -> None:
            self._start_reload(path, listing)

        def error(exc: Exception) -> None:
            self._reloading = False
            self._set_status("")
            self._set_error(str(exc))
            self._update_button_states()

        run_background(work, on_success=success, on_error=error)

    def _start_reload(self, path: str, listing: ImmediateListing) -> None:
        if path != self._current_path:
            self._reloading = False
            return
        if listing.error is not None:
            self._reloading = False
            self._set_status("")
            self._set_error(listing.error)
            self._update_button_states()
            return

        self._set_error(None)
        existing_paths = set(listing.subdirs) | {f.path for f in listing.files}
        self._cache.prune_missing_children(path, existing_paths)
        self._cache.replace_files(path, listing.files)
        self._skipped_total = listing.skipped
        self._entries = self._merge_with_cache(path, listing)
        self._reload_total = len(listing.subdirs)
        self._pending_children = set(listing.subdirs)
        self._scan_queue = list(listing.subdirs)
        self._populate_list()
        self._report_reload_progress()

        if not self._scan_queue:
            self._finish_reload()
            return
        for _ in range(min(MAX_CONCURRENT_SCAN_JOBS, len(self._scan_queue))):
            self._start_next_queued_job(path)

    def _start_next_queued_job(self, parent_path: str) -> None:
        if not self._scan_queue:
            return
        subdir_path = self._scan_queue.pop(0)
        self._start_subdir_job(parent_path, subdir_path)

    def _start_subdir_job(self, parent_path: str, subdir_path: str) -> None:
        """Everything for one subdirectory - the entire recursive subtree
        `du` reports for it in one call - is a single independent job, so
        this subfolder's row is never held up by another's pace; whichever
        finishes first renders first. See `DiskScanRepository.
        scan_subdirectory`."""

        def work() -> SubtreeScan:
            return self._scanner.scan_subdirectory(subdir_path)

        def success(scan: SubtreeScan) -> None:
            if parent_path != self._current_path:
                return
            self._cache.replace_subtree(scan, error=scan.warnings)
            self._skipped_total += scan.skipped
            self._on_subdir_job_done(parent_path, subdir_path)

        def error(exc: Exception) -> None:
            if parent_path != self._current_path:
                return
            # A rescan failing outright (dir vanished, permission revoked
            # mid-Reload) shouldn't blank out whatever good total this
            # subfolder already had from a previous successful scan - only
            # the error note is new.
            existing = self._cache.get_entry(subdir_path)
            self._cache.upsert_folder_summary(
                subdir_path,
                parent_path,
                total_bytes=existing.size_bytes if existing else None,
                item_count=existing.item_count if existing else None,
                error=str(exc),
            )
            self._on_subdir_job_done(parent_path, subdir_path)

        run_background(work, on_success=success, on_error=error)

    def _on_subdir_job_done(self, parent_path: str, subdir_path: str) -> None:
        self._pending_children.discard(subdir_path)
        self._refresh_entry(subdir_path)
        self._start_next_queued_job(parent_path)
        self._report_reload_progress()

    def _refresh_entry(self, path: str) -> None:
        entry = self._cache.get_entry(path)
        if entry is None:
            return
        for index, existing in enumerate(self._entries):
            if existing.path == path:
                self._entries[index] = entry
                break
        self._populate_list()

    def _report_reload_progress(self) -> None:
        done = self._reload_total - len(self._pending_children)
        if self._pending_children:
            self._set_status(f"Scanning... ({done}/{self._reload_total})")
            self._update_button_states()
        else:
            self._finish_reload()

    def _finish_reload(self) -> None:
        path = self._current_path
        total_bytes = sum(e.size_bytes or 0 for e in self._entries)
        item_count = sum(e.item_count or 0 for e in self._entries)
        errored = sum(1 for e in self._entries if e.error is not None)
        error_note = f"{errored} item(s) could not be fully scanned" if errored else None
        self._cache.upsert_folder_summary(path, _parent_of(path), total_bytes, item_count, error_note)
        self._folder_entry = self._cache.get_entry(path)
        self._reloading = False
        self._set_status("")
        self._update_header()
        self._populate_list()
        self._update_button_states()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _populate_list(self) -> None:
        selected = self._selected_entry()
        selected_path = selected.path if selected else None

        rows = list(self._entries)
        key_func = _SORT_KEYS[self._sort_column]
        rows.sort(key=lambda e: key_func(e, self._pending_children), reverse=not self._sort_ascending)
        self._visible = rows

        folder_total = self._folder_entry.size_bytes if self._folder_entry else None
        self._list.DeleteAllItems()
        for row, entry in enumerate(rows):
            self._list.InsertItem(row, entry.name)
            self._list.SetItem(row, _COL_TYPE, "Folder" if entry.is_dir else "File")
            self._list.SetItem(row, _COL_SIZE, _size_text(entry, self._pending_children))
            self._list.SetItem(row, _COL_PERCENT, _percent_text(entry, folder_total))
            self._list.SetItem(row, _COL_ITEMS, "-" if entry.item_count is None else f"{entry.item_count:,}")
            if selected_path and entry.path == selected_path:
                self._list.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

        self._update_chart()
        self._update_button_states()

    def _on_chart_mode_changed(self, event: wx.CommandEvent) -> None:
        self._chart_mode = "extension" if self._mode_extension_radio.GetValue() else "children"
        self._update_chart()

    def _update_chart(self) -> None:
        """Keeps the Chart tab's pie in sync with whatever the table is
        showing right now - called from _populate_list so the two views
        are never out of step, and doubles as this chart's "table view
        exists" accessibility relief (dataviz skill: a chart with sub-3:1
        fills against a light surface needs a table alternative; Table is
        right there as the other tab on the same data)."""
        if self._chart_mode == "extension":
            if self._current_path is None:
                items: List[Tuple[str, int]] = []
            else:
                breakdown = self._cache.extension_breakdown(self._current_path)
                items = [
                    (f".{b.extension}" if b.extension else "(no extension)", b.size_bytes) for b in breakdown
                ]
        else:
            items = [(e.name, e.size_bytes or 0) for e in self._entries]
        self._chart_panel.set_items(items)

    def _selected_entry(self) -> Optional[Entry]:
        index = self._list.GetFirstSelected()
        if index == -1 or index >= len(self._visible):
            return None
        return self._visible[index]

    def _on_col_click(self, event: wx.ListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            # Size/% default to "biggest first" (the point of this
            # screen); Name/Type/Items read more naturally ascending.
            self._sort_ascending = column not in (_COL_SIZE, _COL_PERCENT)
        self._update_column_headers()
        self._populate_list()

    def _update_column_headers(self) -> None:
        for index, label in enumerate(self._column_labels):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            column_info = self._list.GetColumn(index)
            column_info.SetText(label)
            self._list.SetColumn(index, column_info)

    def _rebuild_breadcrumb(self) -> None:
        self._breadcrumb_sizer.Clear(delete_windows=True)
        if self._current_path is None:
            self._breadcrumb_sizer.Add(wx.StaticText(self._breadcrumb_panel, label="No folder open"))
        else:
            segments = _breadcrumb_segments(self._current_path)
            for index, (label, full_path) in enumerate(segments):
                is_current = index == len(segments) - 1
                text = wx.StaticText(self._breadcrumb_panel, label=label)
                if not is_current:
                    text.SetForegroundColour(wx.Colour(30, 90, 200))
                    font = text.GetFont()
                    font.SetUnderlined(True)
                    text.SetFont(font)
                    text.SetCursor(wx.Cursor(wx.CURSOR_HAND))
                    text.Bind(wx.EVT_LEFT_DOWN, lambda evt, p=full_path: self.open_folder(p))
                else:
                    font = text.GetFont()
                    font.MakeBold()
                    text.SetFont(font)
                self._breadcrumb_sizer.Add(text, 0, wx.ALIGN_CENTER_VERTICAL)
                if not is_current:
                    self._breadcrumb_sizer.Add(
                        wx.StaticText(self._breadcrumb_panel, label=" / "), 0, wx.ALIGN_CENTER_VERTICAL
                    )
        self._breadcrumb_panel.Layout()

    def _update_header(self) -> None:
        if self._current_path is None:
            self._header_text.SetLabel("Open a folder to see its disk usage.")
            return
        entry = self._folder_entry
        if entry is None or entry.size_bytes is None:
            label = "Not scanned yet - press Reload to compute disk usage."
        else:
            label = f"{format_bytes(entry.size_bytes)} across {entry.item_count or 0:,} item(s), scanned {entry.scanned_at}"
            if entry.error:
                label += f"  ({entry.error})"
        if self._skipped_total:
            label += f"  ·  {self._skipped_total} symlink/other-filesystem item(s) skipped"
        self._header_text.SetLabel(label)

    def _set_status(self, message: str) -> None:
        self._status_text.SetLabel(message)

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    def _update_button_states(self, event: Optional[wx.ListEvent] = None) -> None:
        busy = self._loading or self._reloading
        has_folder = self._current_path is not None
        self._open_btn.Enable(not busy)
        self._up_btn.Enable(not busy and has_folder and _has_parent(self._current_path))
        self._reload_btn.Enable(not busy and has_folder)
        self._reveal_btn.Enable(not busy and self._selected_entry() is not None)


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------
def _size_text(entry: Entry, pending: Set[str]) -> str:
    if entry.error is not None:
        return f"Error: {entry.error}"
    if entry.path in pending:
        return "Scanning..."
    return format_bytes(entry.size_bytes)


def _percent_text(entry: Entry, folder_total: Optional[int]) -> str:
    if entry.size_bytes is None or not folder_total:
        return "-"
    return f"{entry.size_bytes / folder_total * 100:.1f}%"


def _size_sort_key(entry: Entry, pending: Set[str]):
    # Errored or still-scanning rows sort as lowest, so a descending sort
    # ("biggest first", the point of this screen) surfaces real numbers
    # first - same reasoning as ContainersDiskPage's own disk-usage sort.
    if entry.error is not None or entry.path in pending:
        return -1
    return -1 if entry.size_bytes is None else entry.size_bytes


_SORT_KEYS: List[Callable[[Entry, Set[str]], object]] = [
    lambda e, p: e.name.lower(),
    lambda e, p: not e.is_dir,
    _size_sort_key,
    _size_sort_key,  # % of folder is directly proportional to size for a fixed folder total
    lambda e, p: -1 if e.item_count is None else e.item_count,
]


def _breadcrumb_segments(path: str) -> List[Tuple[str, str]]:
    """Walks from `path` up to the filesystem root via `os.path.dirname`,
    then reverses - a generic way to build clickable segments that works
    the same for any absolute path without special-casing "/" separately."""
    segments: List[Tuple[str, str]] = []
    current = path.rstrip(os.sep) or os.sep
    while True:
        label = os.path.basename(current) or current  # root renders as "/"
        segments.append((label, current))
        parent = os.path.dirname(current) or os.sep
        if parent == current:
            break
        current = parent
    segments.reverse()
    return segments


def _parent_of(path: str) -> str:
    normalized = path.rstrip(os.sep) or os.sep
    return os.path.dirname(normalized) or os.sep


def _has_parent(path: str) -> bool:
    normalized = path.rstrip(os.sep) or os.sep
    return _parent_of(path) != normalized


def _reveal_in_file_manager(path: str) -> None:
    """Best-effort "show this in the OS file manager" - opens the
    containing folder for a file, or the folder itself for a directory,
    via each platform's own opener. This is the one function in the app
    with an OS-specific branch (unlike the du/scan layer, there's no
    single cross-platform CLI for this). Never raises into the UI: a
    missing opener (e.g. a minimal Linux install with no desktop
    environment) is swallowed rather than surfaced as an error, since this
    is a convenience action, not a core feature."""
    target = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", target], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", target], check=False)
        else:
            subprocess.run(["explorer", target], check=False)
    except OSError:
        pass
