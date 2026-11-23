import wx

from app.async_task import AsyncTaskRunner
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import db_path, migrations_dir
from app.indexer import SyncResult, sync_in_new_connection
from app.pages import AboutPage
from app.preferences_page import PreferencesPage, claude_projects_dir, opencode_db_path
from app.projects_page import ProjectsPage
from app.providers import build_providers
from app.repositories import IndexRepository, SettingsRepository
from app.sidebar import SIDEBAR_ITEMS, Sidebar
from app.version import get_version

AUTO_SYNC_INTERVAL_MS = 60_000


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My AI Code Viewer", size=(1400, 820))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.settings_repository = SettingsRepository(conn)
        self.index_repository = IndexRepository(conn)
        self._sync_runner = AsyncTaskRunner(self)

        self._build_menu_bar()
        self.CreateStatusBar(2)
        self.SetStatusWidths([-1, 320])
        self.SetStatusText("Ready", 0)

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = Sidebar(
            root_panel,
            on_select=self._on_sidebar_select,
            on_toggle_collapsed=self._on_sidebar_toggle_collapsed,
        )
        root_sizer.Add(self.sidebar, 0, wx.EXPAND)

        # Page order must match SIDEBAR_ITEMS.
        self.book = wx.Simplebook(root_panel)
        self.projects_page = ProjectsPage(
            self.book, self.index_repository, self.settings_repository,
            on_rescan=self.start_sync, on_status=lambda text: self.SetStatusText(text, 0),
        )
        self.preferences_page = PreferencesPage(self.book, self.settings_repository, on_saved=self._on_prefs_saved)
        self.book.AddPage(self.projects_page, "Projects")
        self.book.AddPage(self.preferences_page, "Preferences")
        self.book.AddPage(AboutPage(self.book), "About")
        root_sizer.Add(self.book, 1, wx.EXPAND)
        root_panel.SetSizer(root_sizer)

        if self.settings_repository.get_sidebar_collapsed():
            self.sidebar.set_collapsed(True)

        self.Centre()
        self.Bind(wx.EVT_CLOSE, self._on_close)

        # Show whatever is already archived immediately, then refresh from
        # the tools' live data in the background.
        self.projects_page.restore_last_selection()
        self.start_sync()

        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda evt: self.start_sync(quiet=True), self._timer)
        self._timer.Start(AUTO_SYNC_INTERVAL_MS)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    def start_sync(self, quiet: bool = False) -> None:
        if self._sync_runner.is_busy():
            return
        providers = build_providers(claude_projects_dir(self.settings_repository),
                                    opencode_db_path(self.settings_repository))
        if not quiet:
            self.SetStatusText("Scanning...", 1)

        def progress(text: str) -> None:
            wx.CallAfter(self._set_sync_status, text)

        self._sync_runner.run(
            work=lambda: sync_in_new_connection(providers, db_path(), progress),
            on_success=lambda result: self._on_sync_done(result, quiet),
            on_error=lambda exc: self.SetStatusText(f"Scan failed: {exc}", 1),
        )

    def _set_sync_status(self, text: str) -> None:
        try:
            self.SetStatusText(text, 1)
        except RuntimeError:
            pass  # frame destroyed mid-sync

    def _on_sync_done(self, result: SyncResult, quiet: bool) -> None:
        if result.updated or result.missing or not quiet:
            self.projects_page.refresh_after_sync()
        text = f"{result.scanned} conversations, {result.updated} updated"
        if result.errors:
            text += f", {len(result.errors)} errors"
            self.SetStatusText("Scan errors: " + "; ".join(result.errors[:3]), 0)
        self.SetStatusText(text, 1)

    def _on_prefs_saved(self) -> None:
        self.start_sync()
        self.SetStatusText("Preferences saved", 0)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self) -> None:
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        rescan_id = wx.NewIdRef()
        file_menu.Append(rescan_id, "Rescan\tF5")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Exit\tAlt+F4")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "About...")

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(help_menu, "&Help")
        self.SetMenuBar(menu_bar)

        self.Bind(wx.EVT_MENU, lambda evt: self.start_sync(), id=rescan_id)
        self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_sidebar_select(self, index: int) -> None:
        self.book.ChangeSelection(index)
        self.SetStatusText(f"Viewing: {SIDEBAR_ITEMS[index][0]}", 0)

    def _on_sidebar_toggle_collapsed(self, collapsed: bool) -> None:
        self.settings_repository.set_sidebar_collapsed(collapsed)

    def _on_close(self, event: wx.CloseEvent) -> None:
        self._timer.Stop()
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            f"My AI Code Viewer {get_version()}\n\n"
            "Browse your Claude Code and OpenCode projects and conversations.",
            "About My AI Code Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
