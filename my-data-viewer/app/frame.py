import os
from typing import Callable, List, Optional
from urllib.parse import unquote, urlparse

import wx
import wx.stc as stc

from app.data_explore_page import DataExplorePage
from app.datasources_page import DatasourcesPage
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.models import Datasource, Script
from app.pages import AboutPage
from app.profiles_page import ProfilesPage
from app.repositories import DatasourceRepository, ProfileRepository, ScriptRepository, SettingsRepository
from app.sidebar import Sidebar, SIDEBAR_ITEMS
from app.task_manager import TaskManager, TaskStatus

DEFAULT_PROFILE_NAME = "default"
DATASOURCES_SIDEBAR_INDEX = 1

# Extensions droppable onto the app -> the datasource `type` they map to (see
# DATASOURCE_TYPES / the file wildcards in datasources_dialog.py).
_DROPPABLE_FILE_TYPES = {
    "csv": "csv",
    "json": "json",
    "ndjson": "json",
    "jsonl": "json",
    "db": "sqlite",
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
}

# Controls with their own meaningful native drag/drop or text-drag behavior -
# left alone by the recursive drop-target install below so dropping a file
# onto e.g. the SQL editor still works like dropping it anywhere else, without
# disturbing their own DnD semantics.
_DROP_TARGET_EXCLUDED_TYPES = (wx.TextCtrl, stc.StyledTextCtrl)


def _normalize_dropped_path(raw: str) -> str:
    """Some drag sources hand over a `file://` URI (percent-encoded) rather
    than a plain path, and/or pad it with stray whitespace - normalize both
    before treating it as a filesystem path."""
    path = raw.strip()
    if path.startswith("file://"):
        path = unquote(urlparse(path).path)
    return path


class _FileDropTarget(wx.FileDropTarget):
    def __init__(self, on_files_dropped: Callable[[List[str]], None]) -> None:
        super().__init__()
        self._on_files_dropped = on_files_dropped

    def OnDropFiles(self, x: int, y: int, filenames: List[str]) -> bool:
        self._on_files_dropped(filenames)
        return True


class TaskStatusBar(wx.StatusBar):
    """Standard two-field wx.StatusBar, plus a Cancel button overlaid on the
    right field - shown only while TaskManager reports a task RUNNING/
    CANCELING, so the app's one background task (export, running a script)
    is always visible and stoppable no matter which page is on screen. A
    plain child wx.Button positioned in EVT_SIZE (there's no native way to
    put a real widget inside a status bar field) rather than a second frame-
    level toolbar, so it doesn't compete for space with page content."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetFieldsCount(2)
        self.SetStatusWidths([-1, 110])

        self._cancel_btn = wx.Button(self, label="Cancel", size=(100, -1))
        self._cancel_btn.Hide()

        self.Bind(wx.EVT_SIZE, self._on_size)
        self._on_size(None)

    def _on_size(self, event: Optional[wx.SizeEvent]) -> None:
        rect = self.GetFieldRect(1)
        self._cancel_btn.SetPosition((rect.x + 2, rect.y + 2))
        self._cancel_btn.SetSize((rect.width - 4, rect.height - 4))
        if event is not None:
            event.Skip()

    def bind_cancel(self, handler: Callable[[wx.CommandEvent], None]) -> None:
        self._cancel_btn.Bind(wx.EVT_BUTTON, handler)

    def show_idle(self, text: str) -> None:
        self.SetStatusText(text, 0)
        self.SetStatusText("", 1)
        self._cancel_btn.Hide()

    def show_task(self, status: TaskStatus, label: str) -> None:
        verb = "Canceling" if status == TaskStatus.CANCELING else "Running"
        self.SetStatusText(f"{verb}: {label}", 0)
        self._cancel_btn.Show()
        self._cancel_btn.Enable(status == TaskStatus.RUNNING)


class UnsavedScriptsDialog(wx.Dialog):
    """Shown on app exit when one or more scripts have unsaved edits (across
    any datasource, not just the currently displayed one)."""

    ID_SAVE_ALL = wx.NewIdRef()
    ID_DISCARD_ALL = wx.NewIdRef()

    def __init__(self, parent: wx.Window, script_names: List[str]) -> None:
        super().__init__(parent, title="Unsaved scripts")

        sizer = wx.BoxSizer(wx.VERTICAL)

        names = ", ".join(script_names)
        message = wx.StaticText(
            self, label=f"There are unsaved scripts: {names}.\nHow would you like to continue?"
        )
        message.Wrap(380)
        sizer.Add(message, 0, wx.ALL, 16)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = wx.Button(self, id=wx.ID_CANCEL, label="Cancel")
        discard_btn = wx.Button(self, id=self.ID_DISCARD_ALL, label="Discard All")
        save_btn = wx.Button(self, id=self.ID_SAVE_ALL, label="Save All")
        btn_sizer.Add(cancel_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(discard_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(save_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 16)

        self.SetSizerAndFit(sizer)

        cancel_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CANCEL))
        discard_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(self.ID_DISCARD_ALL))
        save_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(self.ID_SAVE_ALL))
        save_btn.SetDefault()


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My Data Viewer", size=(1024, 640))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.profile_repository = ProfileRepository(conn)
        self.settings_repository = SettingsRepository(conn)
        self.datasource_repository = DatasourceRepository(conn)
        self.script_repository = ScriptRepository(conn)

        self.active_profile_id = self._bootstrap_active_profile()

        self.task_manager = TaskManager()

        self._build_menu_bar()
        self.status_bar = TaskStatusBar(self)
        self.SetStatusBar(self.status_bar)
        self.status_bar.bind_cancel(lambda evt: self.task_manager.cancel())
        self.task_manager.subscribe(self._on_task_status_changed)
        self.status_bar.show_idle("Ready")

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = Sidebar(root_panel, on_select=self._on_sidebar_select)
        root_sizer.Add(self.sidebar, 0, wx.EXPAND)

        self.book = wx.Simplebook(root_panel)
        self.profiles_page = ProfilesPage(
            self.book,
            self.profile_repository,
            get_active_profile_id=lambda: self.active_profile_id,
            on_activate=self._on_activate_profile,
            on_profiles_changed=self._on_profiles_changed,
        )
        self.datasources_page = DatasourcesPage(
            self.book,
            self.datasource_repository,
            self.active_profile_id,
            self.task_manager,
            on_connected=self._on_datasource_connected,
        )
        self.book.AddPage(self.profiles_page, "Profiles")
        self.book.AddPage(self.datasources_page, "Datasources")
        self.book.AddPage(AboutPage(self.book), "About")

        # Not a sidebar destination - reached only via "Connect" on Datasources.
        self.data_explore_page = DataExplorePage(
            self.book,
            self.datasource_repository,
            self.script_repository,
            self.task_manager,
            on_back=self._go_to_datasources,
        )
        self.book.AddPage(self.data_explore_page, "Data Explore")
        self.data_explore_page_index = self.book.GetPageCount() - 1

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

        self.Centre()

        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._install_drop_target(self)

        self._restore_last_datasource()

    # ------------------------------------------------------------------
    # Profile bootstrap / switching
    # ------------------------------------------------------------------
    def _bootstrap_active_profile(self) -> int:
        """Ensure at least one profile exists, then resolve which one is
        active: whatever was last stored in settings, falling back to the
        first profile if that one no longer exists."""
        profiles = self.profile_repository.list()
        if not profiles:
            profiles = [self.profile_repository.create(DEFAULT_PROFILE_NAME)]

        stored_id = self.settings_repository.get_current_profile_id()
        active = next((p for p in profiles if p.id == stored_id), None) or profiles[0]

        self.settings_repository.set_current_profile_id(active.id)
        return active.id

    def _on_activate_profile(self, profile_id: int) -> None:
        self.active_profile_id = profile_id
        self.settings_repository.set_current_profile_id(profile_id)
        self.datasources_page.set_profile(profile_id)
        self.profiles_page.reload()
        profile = self.profile_repository.get(profile_id)
        self._set_idle_status(f"Active profile: {profile.name if profile else '?'}")

    # ------------------------------------------------------------------
    # Task status (TaskManager) - a global export/script-run task is shown
    # in the status bar regardless of which page is on screen; ordinary page
    # navigation status (below) is only allowed to overwrite it while idle,
    # so switching pages mid-export doesn't hide that it's still running.
    # ------------------------------------------------------------------
    def _on_task_status_changed(self, status: TaskStatus, label: Optional[str]) -> None:
        if status == TaskStatus.IDLE:
            self.status_bar.show_idle("Ready")
        else:
            self.status_bar.show_task(status, label)

    def _set_idle_status(self, text: str) -> None:
        if not self.task_manager.is_busy():
            self.status_bar.show_idle(text)

    def _on_profiles_changed(self) -> None:
        """Called after a profile is created/edited/deleted - re-validate
        that the active profile still exists, falling back (and recreating
        the default profile) if it was the one just deleted."""
        profiles = self.profile_repository.list()
        if not profiles:
            self._on_activate_profile(self.profile_repository.create(DEFAULT_PROFILE_NAME).id)
        elif not any(p.id == self.active_profile_id for p in profiles):
            self._on_activate_profile(profiles[0].id)
        else:
            self.profiles_page.reload()

    # ------------------------------------------------------------------
    # Datasource connect / Data Explore navigation
    # ------------------------------------------------------------------
    def _on_datasource_connected(self, datasource: Datasource) -> None:
        # Deferred via CallAfter: this fires from inside the Connect button's
        # own TaskManager callback (test_connection), before TaskManager has
        # reset itself back to IDLE - load_datasource's own task_manager.start()
        # call (list_tables) would find it still marked busy and be ignored.
        wx.CallAfter(self.data_explore_page.load_datasource, datasource)
        self.book.ChangeSelection(self.data_explore_page_index)
        self.settings_repository.set_last_datasource_id(datasource.id)
        self._set_idle_status(f"Connected to: {datasource.name}")

    def _restore_last_datasource(self) -> None:
        """On startup, jump straight into the last datasource the user had
        open (in the active profile) instead of landing on the Profiles
        screen - mirrors the profile restore in _bootstrap_active_profile,
        but is best-effort: a missing/deleted datasource, or one that now
        belongs to a different profile, just falls back to the normal
        startup screen rather than erroring."""
        datasource_id = self.settings_repository.get_last_datasource_id()
        if datasource_id is None:
            return
        datasource = self.datasource_repository.get(datasource_id)
        if datasource is None or datasource.profile_id != self.active_profile_id:
            return
        self.data_explore_page.load_datasource(datasource)
        self.book.ChangeSelection(self.data_explore_page_index)
        self.sidebar.select(DATASOURCES_SIDEBAR_INDEX)
        self._set_idle_status(f"Connected to: {datasource.name}")

    def _go_to_datasources(self) -> None:
        self.sidebar.select(DATASOURCES_SIDEBAR_INDEX)
        self.book.ChangeSelection(DATASOURCES_SIDEBAR_INDEX)
        self._set_idle_status("Viewing: Datasources")

    # ------------------------------------------------------------------
    # Drag-and-drop: a .csv/.json/.db(sqlite) file dropped anywhere in the app
    # opens (and refreshes) the matching datasource in the current profile,
    # or prefills "New Datasource" if none points at that file yet.
    # ------------------------------------------------------------------
    def _install_drop_target(self, window: wx.Window) -> None:
        if not isinstance(window, _DROP_TARGET_EXCLUDED_TYPES):
            window.SetDropTarget(_FileDropTarget(self._on_files_dropped))
        for child in window.GetChildren():
            self._install_drop_target(child)

    def _on_files_dropped(self, filenames: List[str]) -> None:
        # Deferred via CallAfter: showing a modal dialog (or otherwise
        # reacting) synchronously from inside OnDropFiles runs while the
        # platform's own drag-and-drop loop is still unwinding, which on GTK
        # is known to produce dialogs that pop up but don't paint their
        # initial content correctly. Running after that loop has fully
        # returned avoids it.
        for path in filenames:
            wx.CallAfter(self._handle_dropped_file, path)

    def _handle_dropped_file(self, path: str) -> None:
        path = _normalize_dropped_path(path)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        type_ = _DROPPABLE_FILE_TYPES.get(ext)
        if type_ is None:
            wx.MessageBox(
                f'Unsupported file type: "{os.path.basename(path)}".\n'
                "Only CSV, JSON, and SQLite files can be opened this way.",
                "Open file",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        existing = self.datasource_repository.find_by_file_path(self.active_profile_id, path)
        if existing is not None:
            self.datasources_page.open_existing_datasource(existing)
            return

        self._go_to_datasources()
        self.datasources_page.open_new_datasource_for_file(path, type_)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self) -> None:
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(wx.ID_EXIT, "Exit\tAlt+F4")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "About...")

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(help_menu, "&Help")

        self.SetMenuBar(menu_bar)

        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_sidebar_select(self, index: int) -> None:
        self.book.ChangeSelection(index)
        label = SIDEBAR_ITEMS[index][0]
        self._set_idle_status(f"Viewing: {label}")

    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_close(self, event: wx.CloseEvent) -> None:
        if self.task_manager.is_busy():
            confirm = wx.MessageBox(
                "A task is still running. Exit anyway and abandon it?",
                "Task in progress",
                wx.YES_NO | wx.ICON_WARNING,
                self,
            )
            if confirm != wx.YES:
                if event.CanVeto():
                    event.Veto()
                return

        unsaved = self.data_explore_page.list_unsaved_scripts()
        if not unsaved:
            self.Destroy()
            return

        dlg = UnsavedScriptsDialog(self, [s.name for s in unsaved])
        try:
            choice = dlg.ShowModal()
        finally:
            dlg.Destroy()

        if choice == wx.ID_CANCEL:
            if event.CanVeto():
                event.Veto()
            self._focus_unsaved_script(unsaved[0])
            return
        if choice == UnsavedScriptsDialog.ID_SAVE_ALL:
            self.data_explore_page.save_all_unsaved_scripts()
        elif choice == UnsavedScriptsDialog.ID_DISCARD_ALL:
            self.data_explore_page.discard_all_unsaved_scripts()
        self.Destroy()

    def _focus_unsaved_script(self, script: Script) -> None:
        datasource = self.datasource_repository.get(script.datasource_id)
        if datasource is None:
            return
        self.data_explore_page.load_datasource(datasource)
        self.book.ChangeSelection(self.data_explore_page_index)
        self.data_explore_page.focus_script(script.id)

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            "My Data Viewer\n\n"
            "Explore databases and CSV files: create data sources, browse "
            "their tables/columns, and run SQL against them.",
            "About My Data Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
