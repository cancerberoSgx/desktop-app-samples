from typing import Optional

import wx

from .file_display import FILE_NAME_DISPLAY_OPTIONS


class SettingsDialog(wx.Dialog):
    """File > Settings... - app-wide preferences (not scoped to a profile,
    unlike ProfileDialog). Currently just "File name display", which
    controls how a document's path is rendered in the Documents/Search list
    rows (see file_display.format_display_path) - the full path is still
    always available as a hover tooltip on those rows regardless of this
    setting (see list_ctrl_utils.bind_hover_path_tooltip)."""

    def __init__(self, parent: wx.Window, file_name_display: str) -> None:
        super().__init__(parent, title="Settings", style=wx.DEFAULT_DIALOG_STYLE)
        self._result: Optional[str] = None

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(self, label="File name display:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._display_choice = wx.Choice(self, choices=[label for _key, label in FILE_NAME_DISPLAY_OPTIONS])
        grid.Add(self._display_choice, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)

        note = wx.StaticText(
            self,
            label=(
                "Controls how file names are shown in the Documents and\n"
                "Search views. Hovering a row always shows the full path."
            ),
        )
        note.SetForegroundColour(wx.Colour(120, 120, 120))
        outer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(outer)
        outer.SetSizeHints(self)
        self.Fit()

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

        index = next(
            (i for i, (key, _label) in enumerate(FILE_NAME_DISPLAY_OPTIONS) if key == file_name_display), 0
        )
        self._display_choice.SetSelection(index)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        self._result = FILE_NAME_DISPLAY_OPTIONS[self._display_choice.GetSelection()][0]
        self.EndModal(wx.ID_OK)

    def get_file_name_display(self) -> Optional[str]:
        return self._result
