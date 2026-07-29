import os
from typing import List, Optional

import wx
import wx.grid

from . import drivers
from .models import DATASOURCE_TYPES, Datasource, DatasourceField


class DatasourceDialog(wx.Dialog):
    """Create/edit form for a Datasource - shows a different set of fields
    depending on the selected type (a file picker for csv, connection
    fields for postgres/mysql)."""

    def __init__(
        self,
        parent: wx.Window,
        datasource: Optional[Datasource] = None,
        fields: Optional[List[DatasourceField]] = None,
    ) -> None:
        title = "Edit Datasource" if datasource else "New Datasource"
        super().__init__(parent, title=title, size=(560, 560))
        self._datasource = datasource
        self._result = None
        self._initial_fields = fields or []

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
        sizer.Add(self._file_picker, 0, wx.EXPAND | wx.BOTTOM, 12)

        columns_row = wx.BoxSizer(wx.HORIZONTAL)
        columns_row.Add(wx.StaticText(panel, label="Columns:"), 0, wx.ALIGN_CENTER_VERTICAL)
        columns_row.AddStretchSpacer()
        self._infer_types_btn = wx.Button(panel, label="Infer types")
        columns_row.Add(self._infer_types_btn, 0)
        sizer.Add(columns_row, 0, wx.EXPAND | wx.BOTTOM, 4)

        self._fields_grid = wx.grid.Grid(panel)
        self._fields_grid.CreateGrid(0, 2)
        self._fields_grid.SetColLabelValue(0, "Column name")
        self._fields_grid.SetColLabelValue(1, "Type")
        self._fields_grid.HideRowLabels()
        self._fields_grid.SetColSize(0, 220)
        self._fields_grid.SetColSize(1, 180)
        sizer.Add(self._fields_grid, 1, wx.EXPAND)

        panel.SetSizer(sizer)
        self._set_grid_fields(self._initial_fields)

        self._file_picker.Bind(wx.EVT_FILEPICKER_CHANGED, self._on_csv_file_changed)
        self._infer_types_btn.Bind(wx.EVT_BUTTON, self._on_infer_types)
        return panel

    def _set_grid_fields(self, fields: List[DatasourceField]) -> None:
        grid = self._fields_grid
        current_rows = grid.GetNumberRows()
        if current_rows:
            grid.DeleteRows(0, current_rows)
        if fields:
            grid.AppendRows(len(fields))
        for row, field in enumerate(fields):
            grid.SetCellValue(row, 0, field.name)
            grid.SetReadOnly(row, 0, True)
            grid.SetCellValue(row, 1, field.type)

    def _grid_fields(self) -> List[DatasourceField]:
        grid = self._fields_grid
        fields = []
        for row in range(grid.GetNumberRows()):
            name = grid.GetCellValue(row, 0).strip()
            type_ = grid.GetCellValue(row, 1).strip()
            if name and type_:
                fields.append(DatasourceField(name=name, type=type_, position=row))
        return fields

    def _on_csv_file_changed(self, event: wx.FileDirPickerEvent) -> None:
        if not self._name_ctrl.GetValue().strip():
            base = os.path.splitext(os.path.basename(self._file_picker.GetPath()))[0]
            if base:
                self._name_ctrl.SetValue(base)
        event.Skip()

    def _on_infer_types(self, event: wx.CommandEvent) -> None:
        file_path = self._file_picker.GetPath().strip()
        if not file_path:
            wx.MessageBox("Pick a CSV file first.", "Infer types", wx.OK | wx.ICON_WARNING, self)
            return
        try:
            columns = drivers.infer_csv_columns(file_path)
        except Exception as exc:
            wx.MessageBox(
                f"Could not infer types from this CSV:\n\n{exc}",
                "Infer types",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        self._set_grid_fields(
            [DatasourceField(name=col.name, type=col.type, position=i) for i, col in enumerate(columns)]
        )

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

        file_path = None
        db_host = db_name = db_user = db_password = None
        db_port = None

        if selected_type == "csv":
            file_path = self._file_picker.GetPath().strip()
            if not file_path:
                wx.MessageBox("A CSV file path is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
                return
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]
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
            if not name:
                wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
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
            fields=self._grid_fields() if selected_type == "csv" else [],
        )
        self.EndModal(wx.ID_OK)

    def get_datasource(self) -> Optional[Datasource]:
        return self._result
