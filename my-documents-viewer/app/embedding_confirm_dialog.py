from typing import Optional

import wx


class EmbeddingConfirmDialog(wx.Dialog):
    """"Generate embeddings now?" consent prompt for a structured-data
    import against a paid API backend (openai/gemini) - see
    DocumentsPage._decide_embedding_and_import. A custom wx.Dialog rather
    than the wx.MessageDialog + SetYesNoCancelLabels this replaces, because
    it needs one extra control a plain MessageDialog can't host: a "Don't
    ask me again" checkbox. Checking it and picking "Generate embeddings
    now" or "Full-text only for now" (not Cancel) remembers that choice as
    the profile-wide default - see SettingsRepository.get/set_
    embedding_confirm_default - skipping this dialog on every later import
    until reset back to "Always ask" from the Settings dialog."""

    def __init__(self, parent: wx.Window, message: str) -> None:
        super().__init__(parent, title="Generate embeddings?", style=wx.DEFAULT_DIALOG_STYLE)
        self._choice: Optional[str] = None  # "embed" / "text_only", or None for Cancel

        outer = wx.BoxSizer(wx.VERTICAL)

        body = wx.BoxSizer(wx.HORIZONTAL)
        icon = wx.StaticBitmap(self, bitmap=wx.ArtProvider.GetBitmap(wx.ART_QUESTION, wx.ART_MESSAGE_BOX))
        body.Add(icon, 0, wx.RIGHT | wx.ALIGN_TOP, 12)
        body.Add(wx.StaticText(self, label=message), 0)
        outer.Add(body, 0, wx.ALL, 16)

        self._dont_ask_checkbox = wx.CheckBox(self, label="Don't ask me again (remember this choice)")
        outer.Add(self._dont_ask_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        note = wx.StaticText(self, label='Can also be changed later from File > Settings...')
        note.SetForegroundColour(wx.Colour(120, 120, 120))
        outer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        embed_btn = wx.Button(self, label="Generate embeddings now")
        text_only_btn = wx.Button(self, label="Full-text only for now")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Cancel")
        buttons.Add(embed_btn, 0, wx.RIGHT, 8)
        buttons.Add(text_only_btn, 0, wx.RIGHT, 8)
        buttons.Add(cancel_btn, 0)
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 16)

        self.SetSizer(outer)
        outer.SetSizeHints(self)
        self.Fit()

        embed_btn.Bind(wx.EVT_BUTTON, lambda event: self._finish("embed"))
        text_only_btn.Bind(wx.EVT_BUTTON, lambda event: self._finish("text_only"))
        embed_btn.SetDefault()

    def _finish(self, choice: str) -> None:
        self._choice = choice
        self.EndModal(wx.ID_OK)

    def get_choice(self) -> Optional[str]:
        """"embed" / "text_only" if a decision button was clicked, else
        None (Cancel, or the dialog was closed without choosing)."""
        return self._choice

    def get_remember(self) -> bool:
        return self._dont_ask_checkbox.GetValue()
