import os
from typing import Optional

import wx

from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.file_system_service import FileSystemService
from app.folder_explorer_page import FolderExplorerPage
from app.repositories import FavoriteRepository, SettingsRepository
from app.right_sidebar import RightSidebar
from app.settings_dialog import SettingsDialog
from app.sidebar import FavoritesSidebar


class MainFrame(wx.Frame):
    """Composition root: opens the single sqlite3 connection, runs pending
    migrations, builds the repositories/service, and wires up the sidebars +
    explorer. No wx.Simplebook/page-switching, unlike my-redis-viewer's
    Profiles/Data Sources/About sidebar - this app has one main screen
    (FolderExplorerPage); the left sidebar's job is favorite-folder
    shortcuts, not page navigation. About is a plain message box off the
    Help menu.

    There are two independent, independently-collapsible sidebars either
    side of `explorer_page` in `root_sizer` - `sidebar` (left,
    `FavoritesSidebar`) and `right_sidebar` (right, `RightSidebar` - no
    content yet, just the collapsible shell future features will be added
    to). Each collapse state is its own setting
    (`get_sidebar_collapsed`/`get_right_sidebar_collapsed`), so collapsing
    one has no effect on the other."""

    def __init__(self, initial_path: Optional[str] = None) -> None:
        """`initial_path` is the optional command-line target (main.py's
        `sys.argv[1]`, relative or absolute - see _restore_last_folder) -
        None for a normal launch with no argument."""
        super().__init__(None, title="My File Viewer", size=(1100, 680))
        self._initial_path = initial_path

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.favorite_repository = FavoriteRepository(conn)
        self.settings_repository = SettingsRepository(conn)
        self.file_service = FileSystemService()

        self._build_menu_bar()
        self.CreateStatusBar(3)
        # Field 0 stretches (path/status text); fields 1 ("N item(s)") and 2
        # ("Selected: N") are fixed-width, in that left-to-right order -
        # negative width means "proportion of the remaining space" in wx, so
        # -1 vs. a fixed pixel width is what keeps fields 1/2 a constant
        # size while field 0 does the stretching.
        self.GetStatusBar().SetStatusWidths([-1, 220, 120])
        self.SetStatusText("Ready", 0)
        self.SetStatusText("", 1)
        self.SetStatusText("Selected: 0", 2)

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = FavoritesSidebar(
            root_panel,
            on_select=self._on_favorite_selected,
            on_remove=self._on_favorite_removed,
            on_toggle_collapsed=self._on_sidebar_toggle_collapsed,
        )
        root_sizer.Add(self.sidebar, 0, wx.EXPAND)

        self.explorer_page = FolderExplorerPage(
            root_panel,
            self.file_service,
            self.favorite_repository,
            on_folder_opened=self._on_folder_opened,
            on_favorites_changed=self._on_favorites_changed,
            show_hidden=self.settings_repository.get_show_hidden_files(),
            confirm_delete=self.settings_repository.get_confirm_delete(),
            show_extensions=self.settings_repository.get_show_file_extensions(),
            glob_pattern=self.settings_repository.get_glob_pattern(),
            on_selection_changed=self._on_selection_changed,
            on_item_count_changed=self._on_item_count_changed,
        )
        root_sizer.Add(self.explorer_page, 1, wx.EXPAND)

        self.right_sidebar = RightSidebar(
            root_panel,
            on_apply_pattern=self._on_apply_pattern,
            on_toggle_collapsed=self._on_right_sidebar_toggle_collapsed,
        )
        root_sizer.Add(self.right_sidebar, 0, wx.EXPAND)

        root_panel.SetSizer(root_sizer)

        self.sidebar.refresh(self.favorite_repository.list())
        if self.settings_repository.get_sidebar_collapsed():
            self.sidebar.set_collapsed(True)
        if self.settings_repository.get_right_sidebar_collapsed():
            self.right_sidebar.set_collapsed(True)
        self.right_sidebar.set_pattern(self.settings_repository.get_glob_pattern() or "")

        self.Centre()
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self._restore_last_folder()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def _restore_last_folder(self) -> None:
        """Opens the command-line target passed to main.py, if there was
        one and it actually resolves to a file or folder (relative paths
        resolve against the process's cwd at startup, same as any other
        CLI tool - see FolderExplorerPage.open_path) - a file's *parent*
        folder is opened with the file selected/scrolled into view, a
        folder opens directly. Otherwise (no argument, or one that didn't
        resolve to anything real) falls back to whichever folder was last
        open, if it still exists, or the user's home directory on first
        run or if that folder was since removed/renamed - the same silent,
        no-error-popup fallback either way, since a stale CLI argument
        shouldn't be more disruptive on startup than a stale remembered
        folder already is."""
        if self._initial_path is not None and self.explorer_page.open_path(self._initial_path):
            return
        last_path = self.settings_repository.get_last_folder_path()
        path = last_path if last_path and os.path.isdir(last_path) else os.path.expanduser("~")
        self.explorer_page.open_folder(path)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self) -> None:
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        self._quick_search_menu_item = file_menu.Append(wx.ID_ANY, "Quick Search\tCtrl+P")
        file_menu.AppendSeparator()
        self._open_menu_item = file_menu.Append(wx.ID_OPEN, "Open\tEnter")
        self._rename_menu_item = file_menu.Append(wx.ID_ANY, "Rename\tF2")
        self._delete_menu_item = file_menu.Append(wx.ID_DELETE, "Delete\tDel")
        file_menu.AppendSeparator()
        self._properties_menu_item = file_menu.Append(wx.ID_PROPERTIES, "Properties...")
        # Disabled until a selection actually makes one of these legal - see
        # _on_selection_changed, the one place that updates all four (kept
        # in sync with the tree's own context menu the same way, per
        # FolderTreeCtrl's docstring on why the enabled/disabled policy for
        # these actions lives in FolderExplorerPage/MainFrame, not the tree).
        self._open_menu_item.Enable(False)
        self._rename_menu_item.Enable(False)
        self._delete_menu_item.Enable(False)
        self._properties_menu_item.Enable(False)
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_PREFERENCES, "Settings...")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Exit\tAlt+F4")

        edit_menu = wx.Menu()
        edit_menu.Append(wx.ID_COPY, "Copy Paths\tCtrl+C")
        edit_menu.Append(wx.ID_PASTE, "Paste\tCtrl+V")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "About...")

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(edit_menu, "&Edit")
        menu_bar.Append(help_menu, "&Help")

        self.SetMenuBar(menu_bar)

        self.Bind(wx.EVT_MENU, self._on_quick_search, self._quick_search_menu_item)
        self.Bind(wx.EVT_MENU, self._on_menu_open, self._open_menu_item)
        self.Bind(wx.EVT_MENU, self._on_menu_rename, self._rename_menu_item)
        self.Bind(wx.EVT_MENU, self._on_menu_delete, self._delete_menu_item)
        self.Bind(wx.EVT_MENU, self._on_menu_properties, self._properties_menu_item)
        self.Bind(wx.EVT_MENU, self._on_settings, id=wx.ID_PREFERENCES)
        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_copy_paths, id=wx.ID_COPY)
        self.Bind(wx.EVT_MENU, self._on_paste, id=wx.ID_PASTE)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_folder_opened(self, path: str) -> None:
        self.settings_repository.set_last_folder_path(path)
        self.SetStatusText(f"Viewing: {path}", 0)
        self.sidebar.set_selected_path(path)

    def _on_favorite_selected(self, path: str) -> None:
        self.explorer_page.open_folder(path)

    def _on_favorite_removed(self, favorite_id: int) -> None:
        self.favorite_repository.remove(favorite_id)
        self._on_favorites_changed()

    def _on_favorites_changed(self) -> None:
        self.sidebar.refresh(self.favorite_repository.list())
        self.sidebar.set_selected_path(self.explorer_page.current_path)
        self.explorer_page.sync_favorite_state()

    def _on_sidebar_toggle_collapsed(self, collapsed: bool) -> None:
        self.settings_repository.set_sidebar_collapsed(collapsed)

    def _on_right_sidebar_toggle_collapsed(self, collapsed: bool) -> None:
        self.settings_repository.set_right_sidebar_collapsed(collapsed)

    def _on_apply_pattern(self, pattern: str) -> None:
        """RightSidebar's Patterns section - Apply/Enter or Clear, both
        funnel through this one callback (see RightSidebar's docstring).
        An empty/whitespace-only pattern is stored and applied as `None`
        ("no pattern"), same "blank means cleared" convention
        get_last_folder_path already uses for its own free-text setting."""
        normalized = pattern.strip() or None
        self.settings_repository.set_glob_pattern(normalized)
        self.explorer_page.set_glob_pattern(normalized)

    def _on_item_count_changed(self, text: str) -> None:
        self.SetStatusText(text, 1)

    def _on_selection_changed(self, count: int) -> None:
        self.SetStatusText(f"Selected: {count}", 2)
        # Open/Rename/Properties only make sense for exactly one selected
        # row; Delete is fine for any non-empty selection - kept in sync
        # with the context menu's own enabled state in
        # FolderExplorerPage._on_tree_context_menu.
        self._open_menu_item.Enable(count == 1)
        self._rename_menu_item.Enable(count == 1)
        self._delete_menu_item.Enable(count >= 1)
        self._properties_menu_item.Enable(count == 1)

    def _on_quick_search(self, event: wx.CommandEvent) -> None:
        self.explorer_page.enter_quick_search_mode()

    def _on_menu_open(self, event: wx.CommandEvent) -> None:
        self.explorer_page.open_selected()

    def _on_menu_rename(self, event: wx.CommandEvent) -> None:
        self.explorer_page.rename_selected()

    def _on_menu_delete(self, event: wx.CommandEvent) -> None:
        self.explorer_page.delete_selected()

    def _on_menu_properties(self, event: wx.CommandEvent) -> None:
        self.explorer_page.show_properties_for_selected()

    def _on_settings(self, event: wx.CommandEvent) -> None:
        dialog = SettingsDialog(
            self,
            show_hidden_files=self.settings_repository.get_show_hidden_files(),
            confirm_delete=self.settings_repository.get_confirm_delete(),
            show_file_extensions=self.settings_repository.get_show_file_extensions(),
        )
        try:
            if dialog.ShowModal() == wx.ID_OK:
                show_hidden = dialog.get_show_hidden_files()
                self.settings_repository.set_show_hidden_files(show_hidden)
                self.explorer_page.set_show_hidden(show_hidden)

                confirm_delete = dialog.get_confirm_delete()
                self.settings_repository.set_confirm_delete(confirm_delete)
                self.explorer_page.set_confirm_delete(confirm_delete)

                show_file_extensions = dialog.get_show_file_extensions()
                self.settings_repository.set_show_file_extensions(show_file_extensions)
                self.explorer_page.set_show_extensions(show_file_extensions)
        finally:
            dialog.Destroy()

    def _on_copy_paths(self, event: wx.CommandEvent) -> None:
        self.explorer_page.copy_selected_paths()

    def _on_paste(self, event: wx.CommandEvent) -> None:
        self.explorer_page.try_paste_navigate()

    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            "My File Viewer\n\n"
            "A performant file explorer: pin folders as favorites in the\n"
            "sidebar, browse a folder's contents in a sortable table\n"
            "(Name, Size, Modified), and pick up right where you left off\n"
            "next time you open the app.\n\n"
            "  - Favorites and the last-opened folder are stored locally in\n"
            "    ~/.my-file-viewer (SQLite, schema managed via .sql\n"
            "    migration files).\n"
            "  - Every folder action runs on a background thread\n"
            "    (see app/file_system_service.py + app/async_task.py) so a\n"
            "    slow or network-mounted folder never freezes the window.\n\n"
            "Built with wxPython (https://wxpython.org).",
            "About My File Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
