import wx

from .profiles_dialog import ProfileDialog
from .repositories import ProfileRepository

ACTIVE_MARKER = "✓"


class ProfilesPage(wx.Panel):
    """List/CRUD screen for profiles, plus "Activate" to switch which
    profile's datasources the rest of the app currently shows."""

    def __init__(self, parent, repository: ProfileRepository, get_active_profile_id, on_activate, on_profiles_changed):
        super().__init__(parent)
        self._repository = repository
        self._get_active_profile_id = get_active_profile_id
        self._on_activate = on_activate
        self._on_profiles_changed = on_profiles_changed
        self._profiles = []

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
        self._list.InsertColumn(0, "Active", width=60)
        self._list.InsertColumn(1, "Name", width=200)
        self._list.InsertColumn(2, "Created", width=160)
        self._list.InsertColumn(3, "Updated", width=160)
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

    def reload(self):
        self._profiles = self._repository.list()
        active_id = self._get_active_profile_id()

        self._list.DeleteAllItems()
        for row, profile in enumerate(self._profiles):
            self._list.InsertItem(row, ACTIVE_MARKER if profile.id == active_id else "")
            self._list.SetItem(row, 1, profile.name)
            self._list.SetItem(row, 2, profile.created_at or "")
            self._list.SetItem(row, 3, profile.updated_at or "")

        self._update_button_states(None)

    def _selected_profile(self):
        index = self._list.GetFirstSelected()
        if index == -1:
            return None
        return self._profiles[index]

    def _update_button_states(self, event):
        profile = self._selected_profile()
        has_selection = profile is not None
        self._edit_btn.Enable(has_selection)
        self._delete_btn.Enable(has_selection)
        is_active = has_selection and profile.id == self._get_active_profile_id()
        self._activate_btn.Enable(has_selection and not is_active)

    def _on_new(self, event):
        dlg = ProfileDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._repository.create(dlg.get_name())
            self._on_profiles_changed()
        dlg.Destroy()

    def _on_edit(self, event):
        profile = self._selected_profile()
        if profile is None:
            return
        dlg = ProfileDialog(self, profile)
        if dlg.ShowModal() == wx.ID_OK:
            profile.name = dlg.get_name()
            self._repository.update(profile)
            self._on_profiles_changed()
        dlg.Destroy()

    def _on_delete(self, event):
        profile = self._selected_profile()
        if profile is None:
            return
        confirm = wx.MessageBox(
            f'Delete profile "{profile.name}"? Its datasources will be deleted too.',
            "Confirm delete",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._repository.delete(profile.id)
            self._on_profiles_changed()

    def _on_activate_clicked(self, event):
        profile = self._selected_profile()
        if profile is None:
            return
        self._on_activate(profile.id)
