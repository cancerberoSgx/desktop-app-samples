from typing import Optional

import wx

from .models import DATASOURCE_TYPES, Datasource


class DatasourceDialog(wx.Dialog):
    """Create/edit form for a Datasource - shows a different set of fields
    depending on the selected type (a file picker for csv, connection
    fields for postgres/mysql)."""

    def __init__(self, parent: wx.Window, datasource: Optional[Datasource] = None) -> None:
        title = "Edit Datasource" if datasource else "New Datasource"
        super().__init__(parent, title=title, size=(460, 400))
        self._datasource = datasource
        self._result = None

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._name_ctrl = wx.TextCtrl(self, value=datasource.name if datasource else "")
        grid.Add(self._name_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Type:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._type_choice = wx.Choice(self, choices=list(DATASOURCE_TYPES))
        self._type_choice.SetSelection(
            DATASOURCE_TYPES.index(datasource.type) if datasource else DATASOURCE_TYPES.index("csv")
        )
        grid.Add(self._type_choice, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)

        self._book = wx.Simplebook(self)
        self._book.AddPage(self._build_csv_panel(datasource), "csv")
        self._book.AddPage(self._build_db_panel(datasource), "db")
        outer.Add(self._book, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)

        self.SetSizer(outer)

        self._type_choice.Bind(wx.EVT_CHOICE, self._on_type_changed)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

        self._on_type_changed(None)

    def _build_csv_panel(self, datasource: Optional[Datasource]) -> wx.Panel:
        panel = wx.Panel(self._book)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label="CSV file:"), 0, wx.BOTTOM, 4)
        initial = datasource.file_path if (datasource and datasource.file_path) else ""
        self._file_picker = wx.FilePickerCtrl(
            panel,
            path=initial,
            wildcard="CSV files (*.csv)|*.csv|All files (*.*)|*.*",
            style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN | wx.FLP_FILE_MUST_EXIST,
        )
        sizer.Add(self._file_picker, 0, wx.EXPAND)
        panel.SetSizer(sizer)
        return panel

    def _build_db_panel(self, datasource: Optional[Datasource]) -> wx.Panel:
        panel = wx.Panel(self._book)
        grid = wx.FlexGridSizer(cols=2, gap=(8, 8))
        grid.AddGrowableCol(1, 1)

        def add_field(label: str, value: str, password: bool = False) -> wx.TextCtrl:
            grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            ctrl = wx.TextCtrl(panel, value=value, style=wx.TE_PASSWORD if password else 0)
            grid.Add(ctrl, 1, wx.EXPAND)
            return ctrl

        self._host_ctrl = add_field("Host:", datasource.db_host if datasource and datasource.db_host else "")
        self._port_ctrl = add_field(
            "Port:", str(datasource.db_port) if datasource and datasource.db_port else ""
        )
        self._dbname_ctrl = add_field(
            "Database:", datasource.db_name if datasource and datasource.db_name else ""
        )
        self._user_ctrl = add_field("User:", datasource.db_user if datasource and datasource.db_user else "")
        self._password_ctrl = add_field(
            "Password:",
            datasource.db_password if datasource and datasource.db_password else "",
            password=True,
        )

        panel.SetSizer(grid)
        return panel

    def _on_type_changed(self, event: Optional[wx.CommandEvent]) -> None:
        selected_type = self._type_choice.GetStringSelection()
        self._book.SetSelection(0 if selected_type == "csv" else 1)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        name = self._name_ctrl.GetValue().strip()
        selected_type = self._type_choice.GetStringSelection()

        if not name:
            wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        file_path = None
        db_host = db_name = db_user = db_password = None
        db_port = None

        if selected_type == "csv":
            file_path = self._file_picker.GetPath().strip()
            if not file_path:
                wx.MessageBox("A CSV file path is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
                return
        else:
            db_host = self._host_ctrl.GetValue().strip() or None
            db_name = self._dbname_ctrl.GetValue().strip() or None
            db_user = self._user_ctrl.GetValue().strip() or None
            db_password = self._password_ctrl.GetValue() or None
            port_text = self._port_ctrl.GetValue().strip()
            if port_text:
                try:
                    db_port = int(port_text)
                except ValueError:
                    wx.MessageBox("Port must be a number.", "Validation error", wx.OK | wx.ICON_WARNING, self)
                    return

        self._result = Datasource(
            id=self._datasource.id if self._datasource else None,
            name=name,
            type=selected_type,
            profile_id=self._datasource.profile_id if self._datasource else None,
            file_path=file_path,
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
        )
        self.EndModal(wx.ID_OK)

    def get_datasource(self) -> Optional[Datasource]:
        return self._result
