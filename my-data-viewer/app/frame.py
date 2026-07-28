import wx

from app.datasources_page import DatasourcesPage
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.pages import AboutPage
from app.repositories import DatasourceRepository
from app.sidebar import Sidebar, SIDEBAR_ITEMS


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="My Data Viewer", size=(1024, 640))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.repository = DatasourceRepository(conn)

        self._build_menu_bar()
        self.CreateStatusBar()
        self.SetStatusText("Ready")

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = Sidebar(root_panel, on_select=self._on_sidebar_select)
        root_sizer.Add(self.sidebar, 0, wx.EXPAND)

        self.book = wx.Simplebook(root_panel)
        self.book.AddPage(DatasourcesPage(self.book, self.repository), "Datasources")
        self.book.AddPage(AboutPage(self.book), "About")

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

        self.Centre()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self):
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
    def _on_sidebar_select(self, index):
        self.book.ChangeSelection(index)
        label = SIDEBAR_ITEMS[index][0]
        self.SetStatusText(f"Viewing: {label}")

    def _on_exit(self, event):
        self.Close()

    def _on_about(self, event):
        wx.MessageBox(
            "My Data Viewer\n\n"
            "Explore databases and CSV files: create data sources, browse "
            "their tables/columns, and run SQL against them.",
            "About My Data Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
