import os
from typing import List, Optional

import wx
import wx.grid

from . import drivers
from .async_task import AsyncTaskRunner
from .models import DATASOURCE_TYPES, Datasource, DatasourceField

# Config for the two file-based datasource types ("csv", "json"), which share
# an (almost) identical dialog panel: a file picker, a name auto-filled from
# the file's basename, and an "Infer types" button populating the same
# name/type grid.
_FILE_KINDS = {
    "csv": {
        "label": "CSV file:",
        "wildcard": "CSV files (*.csv)|*.csv|All files (*.*)|*.*",
        "infer": drivers.infer_csv_columns,
    },
    "json": {
        "label": "JSON file:",
        "wildcard": "JSON files (*.json;*.ndjson;*.jsonl)|*.json;*.ndjson;*.jsonl|All files (*.*)|*.*",
        "infer": drivers.infer_json_columns,
    },
}


class DatasourceDialog(wx.Dialog):
    """Create/edit form for a Datasource - shows a different set of fields
    depending on the selected type (a file picker for csv/json, connection
    fields for postgres/mysql)."""

    def __init__(
        self,
        parent: wx.Window,
        datasource: Optional[Datasource] = None,
        fields: Optional[List[DatasourceField]] = None,
        initial_file_path: Optional[str] = None,
        initial_type: Optional[str] = None,
    ) -> None:
        title = "Edit Datasource" if datasource else "New Datasource"
        super().__init__(parent, title=title, size=(560, 560))
        self._datasource = datasource
        self._result = None
        self._initial_fields = fields or []
        self._async = AsyncTaskRunner(self)
        # Only meaningful when `datasource` is None (e.g. a file dropped onto
        # the app that doesn't match any existing datasource yet) - prefills
        # the file picker/type for a still-to-be-created record.
        self._initial_file_path = initial_file_path
        self._initial_type = initial_type

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        if datasource:
            initial_name = datasource.name
        elif self._initial_file_path:
            initial_name = os.path.splitext(os.path.basename(self._initial_file_path))[0]
        else:
            initial_name = ""
        self._name_ctrl = wx.TextCtrl(self, value=initial_name)
        grid.Add(self._name_ctrl, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Type:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._type_choice = wx.Choice(self, choices=list(DATASOURCE_TYPES))
        default_type = self._initial_type if (not datasource and self._initial_type) else "csv"
        self._type_choice.SetSelection(
            DATASOURCE_TYPES.index(datasource.type) if datasource else DATASOURCE_TYPES.index(default_type)
        )
        grid.Add(self._type_choice, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)

        self._file_pickers = {}
        self._fields_grids = {}
        self._infer_btns = {}

        self._book = wx.Simplebook(self)
        self._book.AddPage(self._build_file_panel(datasource, "csv"), "csv")
        self._book.AddPage(self._build_file_panel(datasource, "json"), "json")
        self._book.AddPage(self._build_db_panel(datasource), "db")
        self._book.AddPage(self._build_sqlite_panel(datasource), "sqlite")
        outer.Add(self._book, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)

        self.SetSizer(outer)

        self._type_choice.Bind(wx.EVT_CHOICE, self._on_type_changed)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

        self._on_type_changed(None)

    def _build_file_panel(self, datasource: Optional[Datasource], kind: str) -> wx.Panel:
        config = _FILE_KINDS[kind]
        panel = wx.Panel(self._book)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label=config["label"]), 0, wx.BOTTOM, 4)
        if datasource and datasource.type == kind and datasource.file_path:
            initial = datasource.file_path
        elif not datasource and self._initial_type == kind and self._initial_file_path:
            initial = self._initial_file_path
        else:
            initial = ""
        file_picker = wx.FilePickerCtrl(
            panel,
            path=initial,
            wildcard=config["wildcard"],
            style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN | wx.FLP_FILE_MUST_EXIST,
        )
        self._file_pickers[kind] = file_picker
        sizer.Add(file_picker, 0, wx.EXPAND | wx.BOTTOM, 12)

        columns_row = wx.BoxSizer(wx.HORIZONTAL)
        columns_row.Add(wx.StaticText(panel, label="Columns:"), 0, wx.ALIGN_CENTER_VERTICAL)
        columns_row.AddStretchSpacer()
        infer_btn = wx.Button(panel, label="Infer types")
        self._infer_btns[kind] = infer_btn
        columns_row.Add(infer_btn, 0)
        sizer.Add(columns_row, 0, wx.EXPAND | wx.BOTTOM, 4)

        fields_grid = wx.grid.Grid(panel)
        fields_grid.CreateGrid(0, 2)
        fields_grid.SetColLabelValue(0, "Column name")
        fields_grid.SetColLabelValue(1, "Type")
        fields_grid.HideRowLabels()
        fields_grid.SetColSize(0, 220)
        fields_grid.SetColSize(1, 180)
        self._fields_grids[kind] = fields_grid
        sizer.Add(fields_grid, 1, wx.EXPAND)

        panel.SetSizer(sizer)
        if datasource and datasource.type == kind:
            self._set_grid_fields(fields_grid, self._initial_fields)

        file_picker.Bind(wx.EVT_FILEPICKER_CHANGED, self._on_file_changed)
        infer_btn.Bind(wx.EVT_BUTTON, lambda evt, kind=kind: self._on_infer_types(kind))
        return panel

    @staticmethod
    def _set_grid_fields(grid: wx.grid.Grid, fields: List[DatasourceField]) -> None:
        current_rows = grid.GetNumberRows()
        if current_rows:
            grid.DeleteRows(0, current_rows)
        if fields:
            grid.AppendRows(len(fields))
        for row, field in enumerate(fields):
            grid.SetCellValue(row, 0, field.name)
            grid.SetReadOnly(row, 0, True)
            grid.SetCellValue(row, 1, field.type)

    @staticmethod
    def _grid_fields(grid: wx.grid.Grid) -> List[DatasourceField]:
        fields = []
        for row in range(grid.GetNumberRows()):
            name = grid.GetCellValue(row, 0).strip()
            type_ = grid.GetCellValue(row, 1).strip()
            if name and type_:
                fields.append(DatasourceField(name=name, type=type_, position=row))
        return fields

    def _on_file_changed(self, event: wx.FileDirPickerEvent) -> None:
        if not self._name_ctrl.GetValue().strip():
            base = os.path.splitext(os.path.basename(event.GetEventObject().GetPath()))[0]
            if base:
                self._name_ctrl.SetValue(base)
        event.Skip()

    def _on_infer_types(self, kind: str) -> None:
        file_path = self._file_pickers[kind].GetPath().strip()
        if not file_path:
            wx.MessageBox(f"Pick a {kind.upper()} file first.", "Infer types", wx.OK | wx.ICON_WARNING, self)
            return

        def on_success(columns) -> None:
            self._set_grid_fields(
                self._fields_grids[kind],
                [DatasourceField(name=col.name, type=col.type, position=i) for i, col in enumerate(columns)],
            )

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f"Could not infer types from this {kind.upper()} file:\n\n{exc}",
                "Infer types",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: _FILE_KINDS[kind]["infer"](file_path),
            on_success=on_success,
            on_error=on_error,
            disable=[self._infer_btns[kind]],
        )

    def _build_sqlite_panel(self, datasource: Optional[Datasource]) -> wx.Panel:
        """SQLite is file-based like csv/json, but - unlike them - the file
        already declares real column types and indexes, so there's no
        "Infer types" grid to confirm/override here."""
        panel = wx.Panel(self._book)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label="SQLite file:"), 0, wx.BOTTOM, 4)
        if datasource and datasource.type == "sqlite" and datasource.file_path:
            initial = datasource.file_path
        elif not datasource and self._initial_type == "sqlite" and self._initial_file_path:
            initial = self._initial_file_path
        else:
            initial = ""
        file_picker = wx.FilePickerCtrl(
            panel,
            path=initial,
            wildcard="SQLite files (*.db;*.sqlite;*.sqlite3)|*.db;*.sqlite;*.sqlite3|All files (*.*)|*.*",
            style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN | wx.FLP_FILE_MUST_EXIST,
        )
        self._sqlite_file_picker = file_picker
        sizer.Add(file_picker, 0, wx.EXPAND)
        panel.SetSizer(sizer)

        file_picker.Bind(wx.EVT_FILEPICKER_CHANGED, self._on_file_changed)
        return panel

    def _build_db_panel(self, datasource: Optional[Datasource]) -> wx.Panel:
        panel = wx.Panel(self._book)
        outer = wx.BoxSizer(wx.VERTICAL)

        has_url = bool(datasource and datasource.url)
        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        self._url_mode_radio = wx.RadioButton(panel, label="Connection URL", style=wx.RB_GROUP)
        self._fields_mode_radio = wx.RadioButton(panel, label="Individual fields")
        (self._url_mode_radio if has_url or not datasource else self._fields_mode_radio).SetValue(True)
        mode_row.Add(self._url_mode_radio, 0, wx.RIGHT, 16)
        mode_row.Add(self._fields_mode_radio, 0)
        outer.Add(mode_row, 0, wx.BOTTOM, 12)

        self._db_book = wx.Simplebook(panel)
        self._db_book.AddPage(self._build_db_url_panel(datasource), "url")
        self._db_book.AddPage(self._build_db_fields_panel(datasource), "fields")
        self._db_book.SetSelection(0 if self._url_mode_radio.GetValue() else 1)
        outer.Add(self._db_book, 1, wx.EXPAND)

        panel.SetSizer(outer)

        self._url_mode_radio.Bind(wx.EVT_RADIOBUTTON, self._on_db_mode_changed)
        self._fields_mode_radio.Bind(wx.EVT_RADIOBUTTON, self._on_db_mode_changed)
        return panel

    def _build_db_url_panel(self, datasource: Optional[Datasource]) -> wx.Panel:
        panel = wx.Panel(self._db_book)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label="Connection URL:"), 0, wx.BOTTOM, 4)
        self._url_ctrl = wx.TextCtrl(panel, value=datasource.url if datasource and datasource.url else "")
        sizer.Add(self._url_ctrl, 0, wx.EXPAND | wx.BOTTOM, 4)
        hint = wx.StaticText(panel, label="e.g. postgresql://myuser:mypassword@localhost/searchmindai")
        hint.SetForegroundColour(wx.Colour(120, 120, 120))
        sizer.Add(hint, 0)
        panel.SetSizer(sizer)
        return panel

    def _build_db_fields_panel(self, datasource: Optional[Datasource]) -> wx.Panel:
        panel = wx.Panel(self._db_book)
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

    def _on_db_mode_changed(self, event: wx.CommandEvent) -> None:
        self._db_book.SetSelection(0 if self._url_mode_radio.GetValue() else 1)

    def _on_type_changed(self, event: Optional[wx.CommandEvent]) -> None:
        selected_type = self._type_choice.GetStringSelection()
        page_index = {"csv": 0, "json": 1, "sqlite": 3}.get(selected_type, 2)
        self._book.SetSelection(page_index)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        name = self._name_ctrl.GetValue().strip()
        selected_type = self._type_choice.GetStringSelection()

        file_path = None
        url = None
        db_host = db_name = db_user = db_password = None
        db_port = None

        if selected_type in ("csv", "json"):
            file_path = self._file_pickers[selected_type].GetPath().strip()
            if not file_path:
                wx.MessageBox(
                    f"A {selected_type.upper()} file path is required.",
                    "Validation error",
                    wx.OK | wx.ICON_WARNING,
                    self,
                )
                return
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]
        elif selected_type == "sqlite":
            file_path = self._sqlite_file_picker.GetPath().strip()
            if not file_path:
                wx.MessageBox(
                    "A SQLite file path is required.", "Validation error", wx.OK | wx.ICON_WARNING, self
                )
                return
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]
        elif self._url_mode_radio.GetValue():
            url = self._url_ctrl.GetValue().strip() or None
            if not url:
                wx.MessageBox("A connection URL is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
                return
            if not name:
                wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
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
            if not name:
                wx.MessageBox("Name is required.", "Validation error", wx.OK | wx.ICON_WARNING, self)
                return

        self._result = Datasource(
            id=self._datasource.id if self._datasource else None,
            name=name,
            type=selected_type,
            profile_id=self._datasource.profile_id if self._datasource else None,
            file_path=file_path,
            url=url,
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
            fields=self._grid_fields(self._fields_grids[selected_type]) if selected_type in ("csv", "json") else [],
        )
        self.EndModal(wx.ID_OK)

    def get_datasource(self) -> Optional[Datasource]:
        return self._result
