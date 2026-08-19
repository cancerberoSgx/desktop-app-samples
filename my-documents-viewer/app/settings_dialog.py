from typing import Optional

import wx

from .file_display import FILE_NAME_DISPLAY_OPTIONS
from .repositories import EMBEDDING_CONFIRM_ALWAYS_EMBED, EMBEDDING_CONFIRM_ALWAYS_TEXT_ONLY, EMBEDDING_CONFIRM_ASK

# Paired with repositories.EMBEDDING_CONFIRM_MODES the same way search_page's
# MODE_LABELS pairs with repositories.SEARCH_MODES - the setting's validated
# keys live in the repository layer, display labels live with the UI.
EMBEDDING_CONFIRM_OPTIONS = [
    (EMBEDDING_CONFIRM_ASK, "Always ask (default)"),
    (EMBEDDING_CONFIRM_ALWAYS_EMBED, "Always generate embeddings"),
    (EMBEDDING_CONFIRM_ALWAYS_TEXT_ONLY, "Always import full-text only"),
]


class SettingsDialog(wx.Dialog):
    """File > Settings... - app-wide preferences (not scoped to a profile,
    unlike ProfileDialog): "File name display", which controls how a
    document's path is rendered in the Documents/Search list rows (see
    file_display.format_display_path) - the full path is still always
    available as a hover tooltip on those rows regardless of this setting
    (see list_ctrl_utils.bind_hover_path_tooltip) - and "Generate embeddings
    on import", the remembered default for DocumentsPage's per-import
    consent prompt (see EmbeddingConfirmDialog) - its own "Don't ask me
    again" checkbox writes the same setting this control does, so either
    can undo the other."""

    def __init__(self, parent: wx.Window, file_name_display: str, embedding_confirm_default: str) -> None:
        super().__init__(parent, title="Settings", style=wx.DEFAULT_DIALOG_STYLE)
        self._file_name_display_result: Optional[str] = None
        self._embedding_confirm_default_result: Optional[str] = None

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 10))
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(self, label="File name display:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._display_choice = wx.Choice(self, choices=[label for _key, label in FILE_NAME_DISPLAY_OPTIONS])
        grid.Add(self._display_choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Generate embeddings on import:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._embedding_confirm_choice = wx.Choice(
            self, choices=[label for _key, label in EMBEDDING_CONFIRM_OPTIONS]
        )
        grid.Add(self._embedding_confirm_choice, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 16)

        note = wx.StaticText(
            self,
            label=(
                "File name display controls how file names are shown in the\n"
                "Documents and Search views. Hovering a row always shows the\n"
                "full path.\n\n"
                "\"Generate embeddings on import\" only applies to profiles\n"
                "using a paid API backend (openai/gemini) - fastembed always\n"
                "embeds automatically, at no cost."
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

        embed_index = next(
            (i for i, (key, _label) in enumerate(EMBEDDING_CONFIRM_OPTIONS) if key == embedding_confirm_default), 0
        )
        self._embedding_confirm_choice.SetSelection(embed_index)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        self._file_name_display_result = FILE_NAME_DISPLAY_OPTIONS[self._display_choice.GetSelection()][0]
        self._embedding_confirm_default_result = EMBEDDING_CONFIRM_OPTIONS[
            self._embedding_confirm_choice.GetSelection()
        ][0]
        self.EndModal(wx.ID_OK)

    def get_file_name_display(self) -> Optional[str]:
        return self._file_name_display_result

    def get_embedding_confirm_default(self) -> Optional[str]:
        return self._embedding_confirm_default_result
