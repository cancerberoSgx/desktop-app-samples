from typing import Callable, List, Optional

import wx

from .models import Profile
from .profiles_dialog import ProfileDialog
from .repositories import DocumentRepository, ProfileRepository

ACTIVE_MARKER = "✓"


class ProfilesPage(wx.Panel):
    """List/CRUD screen for profiles, plus "Activate" to switch which
    profile's documents the Documents/Search screens currently show."""

    def __init__(
        self,
        parent: wx.Window,
        repository: ProfileRepository,
        document_repository: DocumentRepository,
        get_active_profile_id: Callable[[], Optional[int]],
        on_activate: Callable[[int], None],
        on_profiles_changed: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._document_repository = document_repository
        self._get_active_profile_id = get_active_profile_id
        self._on_activate = on_activate
        self._on_profiles_changed = on_profiles_changed
        self._profiles: List[Profile] = []

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Profiles"), 0, wx.ALL, 12)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._new_btn = wx.Button(self, label="New...")
        self._edit_btn = wx.Button(self, label="Edit...")
        self._delete_btn = wx.Button(self, label="Delete")
        toolbar.Add(self._new_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._edit_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._delete_btn, 0)
        toolbar.AddStretchSpacer()
        self._activate_btn = wx.Button(self, label="Activate")
        toolbar.Add(self._activate_btn, 0)
        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Active", width=50)
        self._list.InsertColumn(1, "Name", width=160)
        self._list.InsertColumn(2, "Backend", width=90)
        self._list.InsertColumn(3, "Model", width=220)
        self._list.InsertColumn(4, "Dim", width=60)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._new_btn.Bind(wx.EVT_BUTTON, self._on_new)
        self._edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._activate_btn.Bind(wx.EVT_BUTTON, self._on_activate_clicked)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_edit)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)

        self.reload()

    def reload(self) -> None:
        self._profiles = self._repository.list()
        active_id = self._get_active_profile_id()

        self._list.DeleteAllItems()
        for row, profile in enumerate(self._profiles):
            self._list.InsertItem(row, ACTIVE_MARKER if profile.id == active_id else "")
            self._list.SetItem(row, 1, profile.name)
            self._list.SetItem(row, 2, profile.embedding_backend)
            self._list.SetItem(row, 3, profile.embedding_model)
            self._list.SetItem(row, 4, str(profile.embedding_dim))

        self._update_button_states(None)

    def _selected_profile(self) -> Optional[Profile]:
        index = self._list.GetFirstSelected()
        if index == -1:
            return None
        return self._profiles[index]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        profile = self._selected_profile()
        has_selection = profile is not None
        self._edit_btn.Enable(has_selection)
        self._delete_btn.Enable(has_selection)
        is_active = has_selection and profile.id == self._get_active_profile_id()
        self._activate_btn.Enable(has_selection and not is_active)

    def _on_new(self, event: wx.CommandEvent) -> None:
        dlg = ProfileDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            new_profile = dlg.get_profile()
            created = self._repository.create(new_profile.name)
            created.embedding_backend = new_profile.embedding_backend
            created.embedding_model = new_profile.embedding_model
            created.embedding_dim = new_profile.embedding_dim
            created.openai_api_key = new_profile.openai_api_key
            created.gemini_api_key = new_profile.gemini_api_key
            created.chunk_size = new_profile.chunk_size
            created.chat_backend = new_profile.chat_backend
            created.chat_model = new_profile.chat_model
            self._repository.update(created)
            self._on_profiles_changed()
        dlg.Destroy()

    def _on_edit(self, event: wx.CommandEvent) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        dlg = ProfileDialog(self, profile)
        if dlg.ShowModal() == wx.ID_OK:
            updated = dlg.get_profile()
            embedding_config_changed = (
                updated.embedding_backend != profile.embedding_backend
                or updated.embedding_model != profile.embedding_model
                or updated.embedding_dim != profile.embedding_dim
            )
            self._repository.update(updated)
            if embedding_config_changed:
                self._document_repository.reset_vector_index(profile.id)
                wx.MessageBox(
                    'The embedding model changed for profile "'
                    f'{updated.name}" - its vector index was reset. Use '
                    '"Reindex All" on the Documents screen to restore '
                    "similarity search for it (full-text search is "
                    "unaffected).",
                    "Vector index reset",
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            self._on_profiles_changed()
        dlg.Destroy()

    def _on_delete(self, event: wx.CommandEvent) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        confirm = wx.MessageBox(
            f'Delete profile "{profile.name}"? Its documents will be deleted too.',
            "Confirm delete",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._repository.delete(profile.id)
            self._document_repository.reset_vector_index(profile.id)
            self._on_profiles_changed()

    def _on_activate_clicked(self, event: wx.CommandEvent) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        self._on_activate(profile.id)
