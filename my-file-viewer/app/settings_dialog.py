import wx

"""File > Settings... modal - a plain wx.Dialog with a
CreateButtonSizer(OK|CANCEL), same shape as my-redis-viewer's ProfileDialog,
so a future setting is just another control added to `_build_ui` plus a
getter, not a new pattern."""


class SettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, show_hidden_files: bool, confirm_delete: bool) -> None:
        super().__init__(parent, title="Settings", size=(380, 190))
        self._result_show_hidden = show_hidden_files
        self._result_confirm_delete = confirm_delete

        outer = wx.BoxSizer(wx.VERTICAL)

        self._show_hidden_ctrl = wx.CheckBox(self, label="Show hidden files and folders")
        self._show_hidden_ctrl.SetValue(show_hidden_files)
        outer.Add(self._show_hidden_ctrl, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        self._confirm_delete_ctrl = wx.CheckBox(self, label="Ask for confirmation before deleting")
        self._confirm_delete_ctrl.SetValue(confirm_delete)
        outer.Add(self._confirm_delete_ctrl, 0, wx.ALL, 16)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(outer)

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        self._result_show_hidden = self._show_hidden_ctrl.GetValue()
        self._result_confirm_delete = self._confirm_delete_ctrl.GetValue()
        self.EndModal(wx.ID_OK)

    def get_show_hidden_files(self) -> bool:
        return self._result_show_hidden

    def get_confirm_delete(self) -> bool:
        return self._result_confirm_delete
