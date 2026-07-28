import wx

from app.data_explore_page import DataExplorePage
from app.datasources_page import DatasourcesPage
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.models import Datasource
from app.pages import AboutPage
from app.profiles_page import ProfilesPage
from app.repositories import DatasourceRepository, ProfileRepository, SettingsRepository
from app.sidebar import Sidebar, SIDEBAR_ITEMS

DEFAULT_PROFILE_NAME = "default"
DATASOURCES_SIDEBAR_INDEX = 1


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My Data Viewer", size=(1024, 640))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.profile_repository = ProfileRepository(conn)
        self.settings_repository = SettingsRepository(conn)
        self.datasource_repository = DatasourceRepository(conn)

        self.active_profile_id = self._bootstrap_active_profile()

        self._build_menu_bar()
        self.CreateStatusBar()
        self.SetStatusText("Ready")

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
            on_connected=self._on_datasource_connected,
        )
        self.book.AddPage(self.profiles_page, "Profiles")
        self.book.AddPage(self.datasources_page, "Datasources")
        self.book.AddPage(AboutPage(self.book), "About")

        # Not a sidebar destination - reached only via "Connect" on Datasources.
        self.data_explore_page = DataExplorePage(
            self.book, self.datasource_repository, on_back=self._go_to_datasources
        )
        self.book.AddPage(self.data_explore_page, "Data Explore")
        self.data_explore_page_index = self.book.GetPageCount() - 1

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

        self.Centre()

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
        self.SetStatusText(f"Active profile: {profile.name if profile else '?'}")

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
        self.data_explore_page.load_datasource(datasource)
        self.book.ChangeSelection(self.data_explore_page_index)
        self.SetStatusText(f"Connected to: {datasource.name}")

    def _go_to_datasources(self) -> None:
        self.sidebar.select(DATASOURCES_SIDEBAR_INDEX)
        self.book.ChangeSelection(DATASOURCES_SIDEBAR_INDEX)
        self.SetStatusText("Viewing: Datasources")

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
        self.SetStatusText(f"Viewing: {label}")

    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            "My Data Viewer\n\n"
            "Explore databases and CSV files: create data sources, browse "
            "their tables/columns, and run SQL against them.",
            "About My Data Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
