import wx

from .about_dialog import AboutDialog
from .db.connection import get_connection, vector_search_available
from .db.migrator import run_migrations
from .db.paths import migrations_dir
from .documents_page import DocumentsPage
from .profiles_page import ProfilesPage
from .repositories import DocumentRepository, ProfileRepository, SettingsRepository
from .search_page import SearchPage
from .settings_dialog import SettingsDialog
from .sidebar import Sidebar, SIDEBAR_ITEMS

DEFAULT_PROFILE_NAME = "default"


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="My Documents Viewer", size=(1180, 700))

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        vector_enabled = vector_search_available(conn)
        self._vector_enabled = vector_enabled

        self.profile_repository = ProfileRepository(conn)
        self.settings_repository = SettingsRepository(conn)
        self.document_repository = DocumentRepository(conn, vector_enabled=vector_enabled)

        self.active_profile_id = self._bootstrap_active_profile()
        self._file_name_display = self.settings_repository.get_file_name_display()
        self._embedding_confirm_default = self.settings_repository.get_embedding_confirm_default()

        self._build_menu_bar()
        self.CreateStatusBar(2)
        self.SetStatusWidths([-1, 320])
        self.SetStatusText("Ready", 0)
        self.SetStatusText(
            "Vector search: on" if vector_enabled else "Vector search: unavailable (full-text only)", 1
        )

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
            self.document_repository,
            get_active_profile_id=lambda: self.active_profile_id,
            on_activate=self._on_activate_profile,
            on_profiles_changed=self._on_profiles_changed,
        )
        self.documents_page = DocumentsPage(
            self.book,
            self.document_repository,
            self.profile_repository,
            self.active_profile_id,
            on_status=lambda text: self.SetStatusText(text, 0),
            file_name_display=self._file_name_display,
            embedding_confirm_default=self._embedding_confirm_default,
            on_embedding_confirm_default_changed=self._on_embedding_confirm_default_changed,
        )
        self.search_page = SearchPage(
            self.book,
            self.document_repository,
            self.profile_repository,
            self.active_profile_id,
            file_name_display=self._file_name_display,
        )
        self.book.AddPage(self.profiles_page, "Profiles")
        self.book.AddPage(self.documents_page, "Documents")
        self.book.AddPage(self.search_page, "Search")

        root_sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 0)

        root_panel.SetSizer(root_sizer)

        if self.settings_repository.get_sidebar_collapsed():
            self.sidebar.set_collapsed(True)

        self.Centre()

        self.Bind(wx.EVT_CLOSE, self._on_close)

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
        self.documents_page.set_profile(profile_id)
        self.search_page.set_profile(profile_id)
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
        settings_item = file_menu.Append(wx.ID_PREFERENCES, "Settings...\tCtrl+,")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Exit\tAlt+F4")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "About...")

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(help_menu, "&Help")

        self.SetMenuBar(menu_bar)

        self.Bind(wx.EVT_MENU, self._on_settings, settings_item)
        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_sidebar_select(self, index: int) -> None:
        self.book.ChangeSelection(index)
        label = SIDEBAR_ITEMS[index][0]
        self.SetStatusText(f"Viewing: {label}", 0)

    def _on_sidebar_toggle_collapsed(self, collapsed: bool) -> None:
        self.settings_repository.set_sidebar_collapsed(collapsed)

    def _on_settings(self, event: wx.CommandEvent) -> None:
        dlg = SettingsDialog(self, self._file_name_display, self._embedding_confirm_default)
        if dlg.ShowModal() == wx.ID_OK:
            mode = dlg.get_file_name_display()
            if mode != self._file_name_display:
                self._file_name_display = mode
                self.settings_repository.set_file_name_display(mode)
                self.documents_page.set_file_name_display(mode)
                self.search_page.set_file_name_display(mode)

            embedding_confirm_default = dlg.get_embedding_confirm_default()
            if embedding_confirm_default != self._embedding_confirm_default:
                self._on_embedding_confirm_default_changed(embedding_confirm_default)
        dlg.Destroy()

    def _on_embedding_confirm_default_changed(self, mode: str) -> None:
        # Shared by SettingsDialog's own control and EmbeddingConfirmDialog's
        # "Don't ask me again" checkbox (via DocumentsPage) - either one can
        # undo the other, since both just write this one setting.
        self._embedding_confirm_default = mode
        self.settings_repository.set_embedding_confirm_default(mode)
        self.documents_page.set_embedding_confirm_default(mode)

    def _on_exit(self, event: wx.CommandEvent) -> None:
        self.Close()

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.Destroy()

    def _on_about(self, event: wx.CommandEvent) -> None:
        dlg = AboutDialog(self, self._vector_enabled)
        dlg.ShowModal()
        dlg.Destroy()
