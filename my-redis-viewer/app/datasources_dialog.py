from typing import Optional

import wx

from .models import Datasource

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379


class DatasourceDialog(wx.Dialog):
    """Create/edit form for a Datasource - a Redis connection's name plus its
    host/port/user/password."""

    def __init__(self, parent: wx.Window, datasource: Optional[Datasource] = None) -> None:
        title = "Edit Data Source" if datasource else "New Data Source"
        super().__init__(parent, title=title, size=(420, 300))
        self._datasource = datasource
        self._result = None

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._name_ctrl = wx.TextCtrl(self, value=datasource.name if datasource else "")
        grid.Add(self._name_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Host:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._host_ctrl = wx.TextCtrl(
            self, value=datasource.redis_host if datasource else DEFAULT_HOST
        )
        grid.Add(self._host_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Port:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._port_ctrl = wx.TextCtrl(
            self, value=str(datasource.redis_port) if datasource else str(DEFAULT_PORT)
        )
        grid.Add(self._port_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="User:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._user_ctrl = wx.TextCtrl(
            self, value=(datasource.redis_user or "") if datasource else ""
        )
        grid.Add(self._user_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Password:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._password_ctrl = wx.TextCtrl(
            self,
            value=(datasource.redis_password or "") if datasource else "",
            style=wx.TE_PASSWORD,
        )
        grid.Add(self._password_ctrl, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)
        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(outer)

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        name = self._name_ctrl.GetValue().strip()
        if not name:
            wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        host = self._host_ctrl.GetValue().strip() or DEFAULT_HOST

        port_text = self._port_ctrl.GetValue().strip()
        try:
            port = int(port_text) if port_text else DEFAULT_PORT
        except ValueError:
            wx.MessageBox("Port must be a number.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        user = self._user_ctrl.GetValue().strip() or None
        password = self._password_ctrl.GetValue() or None

        self._result = Datasource(
            id=self._datasource.id if self._datasource else None,
            name=name,
            profile_id=self._datasource.profile_id if self._datasource else None,
            redis_host=host,
            redis_port=port,
            redis_user=user,
            redis_password=password,
        )
        self.EndModal(wx.ID_OK)

    def get_datasource(self) -> Optional[Datasource]:
        return self._result
