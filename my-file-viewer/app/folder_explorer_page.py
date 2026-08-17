import os
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

import wx

from .async_task import AsyncTaskRunner
from .file_system_service import FileSystemService
from .folder_tree_ctrl import FolderTreeCtrl
from .models import FileEntry, FolderListing
from .repositories import FavoriteRepository

"""The main screen: a toolbar + clickable breadcrumb + the sortable folder
contents tree (FolderTreeCtrl). Loading the *currently open* folder's
top-level contents always goes through FileSystemService via AsyncTaskRunner
(see async_task.py's docstring) - never a direct os.scandir/os.stat call from
an event handler - so a slow (e.g. network-mounted) folder can't freeze the
window. Expanding a subfolder *row* goes through the same FileSystemService
method, but via its own throwaway AsyncTaskRunner - see _on_expand_folder -
so several rows can be expanded (and queried) concurrently instead of a
folder's contents being fetched eagerly. This is the pattern every future
folder action (rename, delete, recursive size, glob search, ...) should also
follow: add the blocking method to FileSystemService, then call it through an
AsyncTaskRunner here (or in whatever new page/dialog needs it), the same way
my-redis-viewer's DatasourcesPage._on_connect calls
DatasourceRepository.test_connection.
"""


class FolderExplorerPage(wx.Panel):
    def __init__(
        self,
        parent: wx.Window,
        file_service: FileSystemService,
        favorite_repository: FavoriteRepository,
        on_folder_opened: Optional[Callable[[str], None]] = None,
        on_favorites_changed: Optional[Callable[[], None]] = None,
        show_hidden: bool = False,
        on_selection_changed: Optional[Callable[[int], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._file_service = file_service
        self._favorite_repository = favorite_repository
        self._on_folder_opened = on_folder_opened or (lambda path: None)
        self._on_favorites_changed = on_favorites_changed or (lambda: None)
        self._on_selection_changed = on_selection_changed or (lambda count: None)
        self._async = AsyncTaskRunner(self)

        self._current_path: Optional[str] = None
        self._loading = False
        self._show_hidden = show_hidden
        # Set by open_folder(..., select_path=...) - the entry (a pasted/typed
        # file path's parent folder was just opened) to select once that
        # folder's listing lands; see _on_folder_loaded.
        self._pending_select_path: Optional[str] = None

        self._build_ui()
        self._update_header()
        self._update_button_states()

    @property
    def current_path(self) -> Optional[str]:
        return self._current_path

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._open_btn = wx.Button(self, label="Open Folder...")
        self._up_btn = wx.Button(self, label="Up")
        self._favorite_btn = wx.Button(self, label="☆ Add to Favorites")
        toolbar.Add(self._open_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._up_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._favorite_btn, 0)
        toolbar.AddStretchSpacer()
        self._status_text = wx.StaticText(self, label="")
        self._status_text.SetForegroundColour(wx.Colour(120, 120, 120))
        toolbar.Add(self._status_text, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 12)

        breadcrumb_row = wx.BoxSizer(wx.HORIZONTAL)
        self._breadcrumb_panel = wx.Panel(self)
        self._breadcrumb_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._breadcrumb_panel.SetSizer(self._breadcrumb_sizer)
        breadcrumb_row.Add(self._breadcrumb_panel, 1, wx.EXPAND)
        # Both of these are siblings of _breadcrumb_panel (not inside its
        # sizer), so _rebuild_breadcrumb's Clear(delete_windows=True) -
        # which runs on every navigation - never touches them.
        self._copy_path_btn = wx.Button(self, label="⧉", style=wx.BU_EXACTFIT)
        self._copy_path_btn.SetToolTip("Copy folder path to clipboard")
        breadcrumb_row.Add(self._copy_path_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self._collapse_all_btn = wx.Button(self, label="−", style=wx.BU_EXACTFIT)
        self._collapse_all_btn.SetToolTip("Collapse all expanded folders")
        breadcrumb_row.Add(self._collapse_all_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        outer.Add(breadcrumb_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._header_note = wx.StaticText(self, label="")
        outer.Add(self._header_note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self._error_text.Hide()

        self._list = FolderTreeCtrl(
            self,
            on_activate_entry=self._on_activate_entry,
            on_expand_folder=self._on_expand_folder,
            on_selection_changed=self._on_tree_selection_changed,
        )
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)
        self._rebuild_breadcrumb()

        self._open_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        self._up_btn.Bind(wx.EVT_BUTTON, self._on_up)
        self._favorite_btn.Bind(wx.EVT_BUTTON, self._on_toggle_favorite)
        self._collapse_all_btn.Bind(wx.EVT_BUTTON, self._on_collapse_all)
        self._copy_path_btn.Bind(wx.EVT_BUTTON, self._on_copy_path)
        # Deliberately NOT a second self._list.Bind(EVT_TREELIST_SELECTION_CHANGED, ...)
        # here: in this wx build, binding a second handler for the same
        # (event type, window) pair silently replaces the first rather than
        # adding to it - confirmed by hand-testing (see CLAUDE.md) - which
        # would silently kill FolderTreeCtrl's own internal binding for this
        # same event. _on_tree_selection_changed below is the one place
        # that reacts to it, calling everything else (button states, the
        # "Selected: N" callback) as a plain function call instead.

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def open_folder(self, path: str, select_path: Optional[str] = None) -> None:
        """Open `path` in the explorer: validates it's a real directory,
        then loads its contents asynchronously (see _load_current_folder).
        This is the one entry point both "Open Folder...", double-clicking
        a subfolder row, a sidebar favorite click, and startup's
        last-folder restore all funnel through.

        `select_path`, if given, is an entry inside `path` to select once
        the listing lands (see _on_folder_loaded) - used by
        _navigate_to_pasted_path when a pasted/typed breadcrumb path points
        at a file rather than a folder. Always cleared here (not just set)
        so a stale selection from an earlier paste never leaks into an
        unrelated later navigation."""
        if self._loading:
            return
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            wx.MessageBox(f"'{path}' is not a folder.", "My File Viewer", wx.OK | wx.ICON_ERROR, self)
            return

        self._current_path = path
        self._pending_select_path = select_path
        self._set_error(None)
        self._rebuild_breadcrumb()
        self._update_header()
        self._on_folder_opened(path)
        self._load_current_folder()

    def _on_up(self, event: wx.CommandEvent) -> None:
        if self._current_path is not None and _has_parent(self._current_path):
            self.open_folder(_parent_of(self._current_path))

    def _on_open_folder(self, event: wx.CommandEvent) -> None:
        with wx.DirDialog(
            self, "Choose a folder to browse", style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.open_folder(dlg.GetPath())

    def _on_activate_entry(self, entry: FileEntry) -> None:
        if entry.is_dir:
            self.open_folder(entry.path)
        else:
            _open_with_default_app(entry.path)

    def _on_collapse_all(self, event: wx.CommandEvent) -> None:
        self._list.collapse_all()

    def _on_tree_selection_changed(self, count: int) -> None:
        self._update_button_states()
        self._on_selection_changed(count)

    def _on_copy_path(self, event: wx.CommandEvent) -> None:
        if self._current_path is None:
            return
        if not wx.TheClipboard.Open():
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(self._current_path))
        finally:
            wx.TheClipboard.Close()

    # ------------------------------------------------------------------
    # Copy Paths: Edit > Copy Paths / Ctrl+C (see MainFrame._on_copy_paths)
    # ------------------------------------------------------------------
    def copy_selected_paths(self) -> None:
        """Copies the absolute path of every currently selected row (file
        or folder, however many) to the clipboard, one per line - a no-op
        if nothing is selected. `FileEntry.path` is already absolute (see
        FileSystemService.list_folder), so no extra resolving is needed
        here, unlike the free-text path in try_paste_navigate below."""
        entries = self._list.get_selected_entries()
        if not entries:
            return
        text = "\n".join(entry.path for entry in entries)
        if not wx.TheClipboard.Open():
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(text))
        finally:
            wx.TheClipboard.Close()

    # ------------------------------------------------------------------
    # Paste-a-path: Edit > Paste / Ctrl+V (see MainFrame._on_paste)
    # ------------------------------------------------------------------
    def try_paste_navigate(self) -> None:
        """If the clipboard holds text that resolves to an existing file or
        folder, swap the breadcrumb for a focused text input pre-filled
        with it (see _enter_breadcrumb_edit_mode) instead of navigating
        right away - Enter confirms (_on_breadcrumb_edit_enter), at which
        point _navigate_to_pasted_path does the actual navigating/selecting.
        Anything else on the clipboard is silently ignored - this is a
        convenience shortcut, not a general paste handler, and there's no
        other editable text field in this app for a plain Paste to target."""
        text = _get_clipboard_text()
        if text is None:
            return
        resolved = _resolve_existing_path(text)
        if resolved is None:
            return
        self._enter_breadcrumb_edit_mode(resolved)

    def _enter_breadcrumb_edit_mode(self, path: str) -> None:
        self._breadcrumb_sizer.Clear(delete_windows=True)
        edit = wx.TextCtrl(self._breadcrumb_panel, value=path, style=wx.TE_PROCESS_ENTER)
        self._breadcrumb_sizer.Add(edit, 1, wx.EXPAND)
        self._breadcrumb_panel.Layout()
        edit.Bind(wx.EVT_TEXT_ENTER, self._on_breadcrumb_edit_enter)
        edit.Bind(wx.EVT_KEY_DOWN, self._on_breadcrumb_edit_key_down)
        edit.SetFocus()
        edit.SelectAll()

    def _on_breadcrumb_edit_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._rebuild_breadcrumb()  # discard the edit, no navigation
        else:
            event.Skip()

    def _on_breadcrumb_edit_enter(self, event: wx.CommandEvent) -> None:
        text = event.GetEventObject().GetValue()
        resolved = _resolve_existing_path(text)
        if resolved is None:
            wx.MessageBox(
                f"'{text}' is not a valid file or folder path.", "My File Viewer", wx.OK | wx.ICON_ERROR, self
            )
            self._rebuild_breadcrumb()
            return
        self._navigate_to_pasted_path(resolved)

    def _navigate_to_pasted_path(self, resolved_path: str) -> None:
        """`resolved_path` is already confirmed to exist (see
        _resolve_existing_path) - a folder is opened directly; a file is
        opened via its *parent* folder, with the file selected once that
        folder's listing lands (open_folder's select_path). Either way,
        open_folder's own _rebuild_breadcrumb() call is what swaps the text
        input back for the normal clickable breadcrumb - nothing further
        needed here to "return to normal"."""
        if os.path.isdir(resolved_path):
            self.open_folder(resolved_path)
        else:
            self.open_folder(os.path.dirname(resolved_path), select_path=resolved_path)

    # ------------------------------------------------------------------
    # Async loading - every FileSystemService call goes through
    # AsyncTaskRunner, never straight from an event handler.
    # ------------------------------------------------------------------
    def _load_current_folder(self) -> None:
        path = self._current_path
        self._loading = True
        self._set_status("Loading...")
        self._update_button_states()

        self._async.run(
            work=lambda: self._file_service.list_folder(path, show_hidden=self._show_hidden),
            on_success=lambda listing: self._on_folder_loaded(path, listing),
            on_error=lambda exc: self._on_folder_load_error(str(exc)),
            on_done=self._on_load_done,
            disable=[self._open_btn, self._up_btn],
        )

    def _on_load_done(self) -> None:
        self._loading = False
        self._set_status("")
        self._update_button_states()

    def _on_folder_loaded(self, path: str, listing: FolderListing) -> None:
        if path != self._current_path:
            return  # the user navigated elsewhere before this landed
        if listing.error is not None:
            self._set_error(listing.error)
            self._list.set_root_entries([])
            return
        self._set_error(None)
        self._list.set_root_entries(listing.entries)
        if self._pending_select_path is not None:
            self._list.select_path(self._pending_select_path)
            self._pending_select_path = None
        note = f"  ·  {listing.skipped} item(s) could not be read" if listing.skipped else ""
        self._header_note.SetLabel(
            f"{len(listing.entries)} item(s){note}"
        )

    def _on_folder_load_error(self, message: str) -> None:
        self._set_error(message)
        self._list.set_root_entries([])

    def _on_expand_folder(self, path: str, on_loaded: Callable[[FolderListing], None]) -> None:
        """FolderTreeCtrl's callback for "the user expanded this row, and it
        hasn't been queried yet" - the one place a subfolder's contents get
        fetched, and only in response to that expand. Uses its own
        throwaway AsyncTaskRunner rather than self._async: several rows can
        be expanded before the first fetch lands, and self._async (shared
        with top-level folder navigation) only runs one job at a time - a
        second .run() call while busy is silently ignored (see
        AsyncTaskRunner's docstring) - which would leave some expanded rows
        stuck on "Loading…" forever."""
        runner = AsyncTaskRunner(self)
        runner.run(
            work=lambda: self._file_service.list_folder(path, show_hidden=self._show_hidden),
            on_success=on_loaded,
            on_error=lambda exc: on_loaded(FolderListing(error=str(exc))),
        )

    def set_show_hidden(self, show_hidden: bool) -> None:
        """Applies a new "show hidden files" preference - reloads the
        currently open folder (if any) so it takes effect immediately.
        Already-expanded subfolder rows reset back to collapsed/unqueried,
        same as any other top-level reload (see FolderTreeCtrl.set_root_entries) -
        an acceptable reset for a settings change, which is a deliberate,
        infrequent action, not a hot path."""
        if self._show_hidden == show_hidden:
            return
        self._show_hidden = show_hidden
        if self._current_path is not None:
            self._load_current_folder()

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------
    def _on_toggle_favorite(self, event: wx.CommandEvent) -> None:
        if self._current_path is None:
            return
        existing = self._favorite_repository.get_by_path(self._current_path)
        if existing is not None:
            self._favorite_repository.remove(existing.id)
        else:
            self._favorite_repository.add_folder(self._current_path)
        self._on_favorites_changed()
        self._update_button_states()

    def sync_favorite_state(self) -> None:
        """Refreshes the Add/Remove Favorite button's label - called by
        MainFrame after a favorite is removed from the sidebar, since that
        can change whether the currently-open folder is still a favorite
        without this page's own button ever being clicked."""
        self._update_button_states()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
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
            self._header_note.SetLabel("Open a folder to see its contents.")

    def _set_status(self, message: str) -> None:
        self._status_text.SetLabel(message)

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    def _update_button_states(self, event: Optional[wx.Event] = None) -> None:
        has_folder = self._current_path is not None
        self._open_btn.Enable(not self._loading)
        self._up_btn.Enable(not self._loading and has_folder and _has_parent(self._current_path))
        self._favorite_btn.Enable(has_folder)
        self._collapse_all_btn.Enable(has_folder)
        self._copy_path_btn.Enable(has_folder)
        if has_folder:
            is_favorite = self._favorite_repository.get_by_path(self._current_path) is not None
            self._favorite_btn.SetLabel("★ Remove from Favorites" if is_favorite else "☆ Add to Favorites")


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------
def _breadcrumb_segments(path: str) -> List[Tuple[str, str]]:
    """Walks from `path` up to the filesystem root via `os.path.dirname`,
    then reverses - works the same for any absolute path without
    special-casing "/" separately."""
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


def _get_clipboard_text() -> Optional[str]:
    """Reads plain text off the system clipboard, or None if there isn't
    any - used by try_paste_navigate (Edit > Paste / Ctrl+V)."""
    if not wx.TheClipboard.Open():
        return None
    try:
        if not wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_TEXT)):
            return None
        data = wx.TextDataObject()
        if not wx.TheClipboard.GetData(data):
            return None
        return data.GetText()
    finally:
        wx.TheClipboard.Close()


def _resolve_existing_path(text: str) -> Optional[str]:
    """Best-effort turn a pasted/typed string into an absolute path to an
    existing file or folder, or None if it isn't one - trims whitespace and
    a single layer of surrounding quotes (as when a path is copied quoted
    from a terminal or another file manager) and expands a leading ``~``
    before checking existence."""
    candidate = text.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
        candidate = candidate[1:-1].strip()
    if not candidate:
        return None
    candidate = os.path.expanduser(candidate)
    if not os.path.exists(candidate):
        return None
    return os.path.abspath(candidate)


def _open_with_default_app(path: str) -> None:
    """Best-effort "open this file the way the OS would" - opener/`start`/
    `xdg-open` per platform, spawned (not awaited beyond the opener process
    itself returning, which happens immediately once it's launched the real
    application) so this never blocks the UI thread. Failures are
    swallowed: this is a convenience action, not a core feature, mirroring
    my-disk-viewer's _reveal_in_file_manager."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", path], check=False)
        else:
            os.startfile(path)  # noqa: S606 - Windows-only API
    except OSError:
        pass
