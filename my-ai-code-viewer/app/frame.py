import wx

from app.async_task import AsyncTaskRunner
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import db_path, migrations_dir
from app.indexer import SyncResult, sync_in_new_connection
from app.about_dialog import AboutDialog
from app.preferences_dialog import PreferencesDialog, claude_projects_dir, opencode_db_path
from app.projects_page import ProjectsPage
from app.providers import build_providers
from app.repositories import IndexRepository, SettingsRepository

AUTO_SYNC_INTERVAL_MS = 60_000
DEFAULT_SIZE = (1400, 820)
MIN_SIZE = (400, 300)


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My AI Code Viewer", size=DEFAULT_SIZE)

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.settings_repository = SettingsRepository(conn)
        self.index_repository = IndexRepository(conn)
        self._sync_runner = AsyncTaskRunner(self)
        self._restore_geometry()

        self._build_menu_bar()
        self.CreateStatusBar(2)
        self.SetStatusWidths([-1, 320])
        self.SetStatusText("Ready", 0)

        self.projects_page = ProjectsPage(
            self, self.index_repository, self.settings_repository,
            on_rescan=self.start_sync, on_status=lambda text: self.SetStatusText(text, 0),
        )

        self.Bind(wx.EVT_CLOSE, self._on_close)

        # Show whatever is already archived immediately, then refresh from
        # the tools' live data in the background.
        self.projects_page.restore_last_selection()
        self.start_sync()

        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda evt: self.start_sync(quiet=True), self._timer)
        self._timer.Start(AUTO_SYNC_INTERVAL_MS)

    # ------------------------------------------------------------------
    # Window geometry (remembered across runs)
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        s = self.settings_repository
        width, height = s.get_int("window_width"), s.get_int("window_height")
        x, y = s.get_int("window_x"), s.get_int("window_y")
        if width and height:
            self.SetSize(max(width, MIN_SIZE[0]), max(height, MIN_SIZE[1]))
        # Only reuse the position if it still lands on a connected display.
        if x is not None and y is not None and wx.Display.GetFromPoint(wx.Point(x + 40, y + 40)) != wx.NOT_FOUND:
            self.SetPosition(wx.Point(x, y))
        else:
            self.Centre()
        # The un-maximized rect, tracked so un-maximizing next run restores it.
        self._normal_rect = self.GetRect()
        if s.get_bool("window_maximized"):
            self.Maximize()
        self.Bind(wx.EVT_SIZE, self._on_geometry_changed)
        self.Bind(wx.EVT_MOVE, self._on_geometry_changed)

    def _on_geometry_changed(self, event: wx.Event) -> None:
        event.Skip()
        if not self.IsMaximized() and not self.IsIconized() and not self.IsFullScreen():
            self._normal_rect = self.GetRect()

    def _save_geometry(self) -> None:
        s = self.settings_repository
        rect = self._normal_rect
        s.set_int("window_x", rect.x)
        s.set_int("window_y", rect.y)
        s.set_int("window_width", rect.width)
        s.set_int("window_height", rect.height)
        s.set_bool("window_maximized", self.IsMaximized())

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

    def _on_prefs_saved(self, sources_changed: bool) -> None:
        self.projects_page.view.apply_settings()
        if sources_changed:
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
        export_id = wx.NewIdRef()
        file_menu.Append(export_id, "Export conversation as HTML...\tCtrl+S")
        file_menu.Append(wx.ID_PREFERENCES, "Preferences...\tCtrl+,")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Exit\tAlt+F4")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "About...")

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(help_menu, "&Help")
        self.SetMenuBar(menu_bar)

        self.Bind(wx.EVT_MENU, lambda evt: self.start_sync(), id=rescan_id)
        self.Bind(wx.EVT_MENU, lambda evt: self.projects_page.view.export_html(), id=export_id)
        self.Bind(wx.EVT_UPDATE_UI, lambda evt: evt.Enable(
            hasattr(self, "projects_page") and self.projects_page.view.current_conversation_id() is not None),
            id=export_id)
        self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)
        self.Bind(wx.EVT_MENU, self._on_preferences, id=wx.ID_PREFERENCES)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_close(self, event: wx.CloseEvent) -> None:
        self._timer.Stop()
        self._save_geometry()
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        with AboutDialog(self) as dialog:
            dialog.ShowModal()

    def _on_preferences(self, event: wx.CommandEvent) -> None:
        with PreferencesDialog(self, self.settings_repository, on_saved=self._on_prefs_saved) as dialog:
            dialog.ShowModal()
