from typing import Optional

import wx

from app.data_explorer_page import DataExplorerPage
from app.datasources_page import DatasourcesPage
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.models import Datasource
from app.pages import AboutPage
from app.profiles_page import ProfilesPage
from app.repositories import DatasourceRepository, ProfileRepository, ScriptRepository, SettingsRepository
from app.sidebar import Sidebar, SIDEBAR_ITEMS

DEFAULT_PROFILE_NAME = "default"


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My Redis Viewer", size=(1024, 640))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self.profile_repository = ProfileRepository(conn)
        self.settings_repository = SettingsRepository(conn)
        self.datasource_repository = DatasourceRepository(conn)
        self.script_repository = ScriptRepository(conn)

        self.active_profile_id = self._bootstrap_active_profile()
        self._startup_datasource = self._resolve_last_datasource()

        self._build_menu_bar()
        self.CreateStatusBar(2)
        self.SetStatusWidths([-1, 260])
        self.SetStatusText("Ready", 0)

        root_panel = wx.Panel(self)
        root_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sidebar = Sidebar(
            root_panel,
            on_select=self._on_sidebar_select,
            on_toggle_collapsed=self._on_sidebar_toggle_collapsed,
        )
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
            on_connected=self._on_connect_datasource,
        )
        self.data_explorer_page = DataExplorerPage(
            self.book,
            self.datasource_repository,
            self.script_repository,
            on_status=lambda text: self.SetStatusText(text, 1),
        )
        self.book.AddPage(self.profiles_page, "Profiles")
        self.book.AddPage(self.datasources_page, "Data Sources")
        self.book.AddPage(AboutPage(self.book), "About")
        self._data_explorer_index = self.book.GetPageCount()
        self.book.AddPage(self.data_explorer_page, "Data Explorer")

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

        if self.settings_repository.get_sidebar_collapsed():
            self.sidebar.set_collapsed(True)

        self.Centre()

        self.Bind(wx.EVT_CLOSE, self._on_close)

        if self._startup_datasource is not None:
            # Land directly on the datasource the user last had open in the
            # Data Explorer, instead of the Profiles screen - visually
            # matches what manually clicking Connect from Data Sources
            # would have done, so the sidebar highlight is set to match
            # (index 1 = "Data Sources" - see SIDEBAR_ITEMS).
            self.sidebar.select(1)
            self._on_connect_datasource(self._startup_datasource)

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

    def _resolve_last_datasource(self) -> Optional[Datasource]:
        """If a datasource was last opened in the Data Explorer (see
        _on_connect_datasource), look it up so startup can land directly
        on it instead of the Profiles screen. A remembered datasource is a
        more specific "where the user left off" signal than the
        separately-stored current profile id, so it wins: the active
        profile is switched to match rather than second-guessed, the same
        way it already would be if the user manually navigated to that
        datasource's profile and clicked Connect. Returns None (leaving
        the normal profile-based startup untouched) if nothing was
        recorded, or if the datasource/its profile no longer exist."""
        last_id = self.settings_repository.get_last_datasource_id()
        if last_id is None:
            return None
        datasource = self.datasource_repository.get(last_id)
        if datasource is None or datasource.profile_id is None:
            return None
        if self.profile_repository.get(datasource.profile_id) is None:
            return None

        self.active_profile_id = datasource.profile_id
        self.settings_repository.set_current_profile_id(datasource.profile_id)
        return datasource

    def _on_activate_profile(self, profile_id: int) -> None:
        self.active_profile_id = profile_id
        self.settings_repository.set_current_profile_id(profile_id)
        self.datasources_page.set_profile(profile_id)
        self.profiles_page.reload()
        profile = self.profile_repository.get(profile_id)
        self.SetStatusText(f"Active profile: {profile.name if profile else '?'}", 0)

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
        self.SetStatusText("", 1)

    def _on_sidebar_toggle_collapsed(self, collapsed: bool) -> None:
        self.settings_repository.set_sidebar_collapsed(collapsed)

    def _on_connect_datasource(self, datasource: Datasource) -> None:
        self.book.ChangeSelection(self._data_explorer_index)
        self.SetStatusText(f"Viewing: Data Explorer - {datasource.name}", 0)
        self.data_explorer_page.open_datasource(datasource)
        self.settings_repository.set_last_datasource_id(datasource.id)

    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        wx.MessageBox(
            "My Redis Viewer\n\n"
            "Manage Redis connections: create data sources per profile and "
            "connect to check they're reachable.",
            "About My Redis Viewer",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
