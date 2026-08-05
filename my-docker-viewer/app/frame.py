import wx

from app.containers_page import ContainersPage
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.pages import AboutPage
from app.repositories import ContainerRepository, SettingsRepository
from app.sidebar import Sidebar, SIDEBAR_ITEMS


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My Docker Viewer", size=(1200, 680))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        # Not driving any screen yet - wired up now so future preferences
        # (remembered filters, auto-refresh interval, ...) have a ready-made
        # place to live, same as my-redis-viewer.
        self.settings_repository = SettingsRepository(conn)

        self.container_repository = ContainerRepository()

        self._build_menu_bar()
        self.CreateStatusBar(1)
        self.SetStatusText("Ready", 0)

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = Sidebar(root_panel, on_select=self._on_sidebar_select)
        root_sizer.Add(self.sidebar, 0, wx.EXPAND)

        self.book = wx.Simplebook(root_panel)
        self.containers_page = ContainersPage(self.book, self.container_repository)
        self.book.AddPage(self.containers_page, "Containers")
        self.book.AddPage(AboutPage(self.book), "About")

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

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
    def _on_sidebar_select(self, index: int) -> None:
        self.book.ChangeSelection(index)
        label = SIDEBAR_ITEMS[index][0]
        self.SetStatusText(f"Viewing: {label}", 0)

    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            "My Docker Viewer\n\n"
            "Admin your local Docker containers: list, filter, inspect "
            "resource usage, stop, and remove - all via the docker CLI.",
            "About My Docker Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
