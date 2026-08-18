from pathlib import Path
from typing import Optional

import wx

from .data_import import DataFilePreview, ImportMapping, find_duplicate_id_values, read_records

NONE_CHOICE_LABEL = "(None)"


class ImportMappingDialog(wx.Dialog):
    """Column-mapping form shown after DocumentsPage parses a CSV/JSON file
    (see data_import.preview) and before DocumentRepository.import_data_file
    runs. Lets the user choose which columns become searchable content (vs.
    display-only metadata - every column ends up in properties_json either
    way, see data_import.build_record_text) and which column, if any,
    stably identifies a row across re-imports."""

    def __init__(self, parent: wx.Window, path: Path, preview: DataFilePreview) -> None:
        title = f"Import {path.name}"
        super().__init__(parent, title=title, size=(560, 540), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._path = path
        self._preview = preview
        self._result: Optional[ImportMapping] = None

        outer = wx.BoxSizer(wx.VERTICAL)

        outer.Add(
            wx.StaticText(
                self,
                label=(
                    f"{preview.row_count} row(s), {len(preview.columns)} column(s) found. "
                    f"This will create 1 container document and {preview.row_count} child document(s)."
                ),
            ),
            0,
            wx.EXPAND | wx.ALL,
            12,
        )

        outer.Add(wx.StaticText(self, label="Content columns (searchable text):"), 0, wx.LEFT | wx.RIGHT, 12)
        self._content_list = wx.CheckListBox(self, choices=preview.columns)
        for index in range(len(preview.columns)):
            self._content_list.Check(index, True)
        outer.Add(self._content_list, 1, wx.EXPAND | wx.ALL, 12)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        choice_values = [NONE_CHOICE_LABEL] + preview.columns

        grid.Add(wx.StaticText(self, label="Identifier column:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._id_choice = wx.Choice(self, choices=choice_values)
        self._id_choice.SetSelection(0)
        grid.Add(self._id_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Title column (for display):"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._title_choice = wx.Choice(self, choices=choice_values)
        self._title_choice.SetSelection(0)
        grid.Add(self._title_choice, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        note = wx.StaticText(
            self,
            label=(
                "Without an identifier column, rows are matched across re-imports by\n"
                "their exact content - an edited row will be treated as a new one."
            ),
        )
        note.SetForegroundColour(wx.Colour(120, 120, 120))
        outer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(outer)
        outer.SetSizeHints(self)
        self.Fit()

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        content_columns = [
            self._preview.columns[i] for i in range(len(self._preview.columns)) if self._content_list.IsChecked(i)
        ]
        if not content_columns:
            wx.MessageBox("Choose at least one content column.", "Validation error", wx.OK | wx.ICON_WARNING, self)
            return

        id_index = self._id_choice.GetSelection()
        id_column = self._preview.columns[id_index - 1] if id_index > 0 else None

        if id_column:
            # Re-parse the whole file (the preview only kept a sample) so
            # duplicate values anywhere in the file are actually caught, not
            # just in the first few rows shown above.
            try:
                records = read_records(self._path)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user below
                wx.MessageBox(f"Could not re-read {self._path.name}:\n\n{exc}", "Validation error", wx.OK | wx.ICON_WARNING, self)
                return
            duplicates = find_duplicate_id_values(records, id_column)
            if duplicates:
                sample = ", ".join(duplicates[:5])
                more = f" (+{len(duplicates) - 5} more)" if len(duplicates) > 5 else ""
                wx.MessageBox(
                    f'Column "{id_column}" has duplicate values: {sample}{more}.\n\n'
                    "Choose a different identifier column, or none.",
                    "Validation error",
                    wx.OK | wx.ICON_WARNING,
                    self,
                )
                return

        title_index = self._title_choice.GetSelection()
        title_column = self._preview.columns[title_index - 1] if title_index > 0 else None

        self._result = ImportMapping(content_columns=content_columns, id_column=id_column, title_column=title_column)
        self.EndModal(wx.ID_OK)

    def get_mapping(self) -> Optional[ImportMapping]:
        return self._result
