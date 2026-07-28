import wx

from .models import Profile


class ProfileDialog(wx.Dialog):
    """Create/edit form for a Profile - just a name."""

    def __init__(self, parent, profile: Profile = None):
        title = "Edit Profile" if profile else "New Profile"
        super().__init__(parent, title=title, size=(360, 160))
        self._result = None

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)
        grid.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._name_ctrl = wx.TextCtrl(self, value=profile.name if profile else "")
        grid.Add(self._name_ctrl, 1, wx.EXPAND)
        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(outer)

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, event):
        name = self._name_ctrl.GetValue().strip()
        if not name:
            wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return
        self._result = name
        self.EndModal(wx.ID_OK)

    def get_name(self) -> str:
        return self._result
