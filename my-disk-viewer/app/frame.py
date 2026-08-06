import wx

from app.cache_repository import CacheRepository, SettingsRepository
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.disk_scan_repository import DiskScanRepository
from app.explorer_page import ExplorerPage


class MainFrame(wx.Frame):
    """Composition root - same posture as my-docker-viewer's: opens the
    single sqlite3 connection, runs pending migrations, builds the
    (stateless) DiskScanRepository and the (connection-bound)
    CacheRepository/SettingsRepository, and wires up the one screen this
    app has. No sidebar/wx.Simplebook - unlike my-docker-viewer's five
    resource-type screens, there's only one concept here, so ExplorerPage
    fills the whole window directly."""

    def __init__(self) -> None:
        super().__init__(None, title="My Disk Viewer", size=(1100, 680))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.cache_repository = CacheRepository(conn)
        # Not driving anything yet - wired up ahead of time so a future
        # preference (recent-folders list for the Open Folder toolbar) has
        # a ready-made place to live, same as my-docker-viewer.
        self.settings_repository = SettingsRepository(conn)
        self.scan_repository = DiskScanRepository()

        self._build_menu_bar()
        self.CreateStatusBar(1)
        self.SetStatusText("Ready", 0)

        self.explorer_page = ExplorerPage(self, self.scan_repository, self.cache_repository)
        root_sizer = wx.BoxSizer(wx.VERTICAL)
        root_sizer.Add(self.explorer_page, 1, wx.EXPAND)
        self.SetSizer(root_sizer)

        self.Centre()
        self.Bind(wx.EVT_CLOSE, self._on_close)

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
    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            "My Disk Viewer\n\n"
            "Visualize disk usage in a folder recursively - which subfolders "
            "and file types are using the most space - so you can find what's "
            "actually worth deleting. Read-only: it never deletes anything "
            "itself, only reveals files/folders in your OS file manager. Disk "
            "usage is measured with `du` (Linux/macOS) and cached in SQLite so "
            "revisiting a folder is instant; Reload re-scans it.\n\n"
            "Author: Sebastián Gurin (cancerberoSgx)\n"
            "License: MIT\n"
            "Home: https://github.com/cancerberoSgx/desktop-app-samples",
            "About My Disk Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
