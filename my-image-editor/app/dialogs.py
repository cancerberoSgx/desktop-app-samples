"""Small modal dialogs: the text of a text shape, and Image > Resize."""
from typing import Optional, Tuple

import wx

from app.editor import fonts
from app.editor.document import MAX_DIMENSION, RESAMPLE_FILTERS
from app.editor.shapes import Style

_WX_FAMILIES = {fonts.SANS: wx.FONTFAMILY_SWISS, fonts.SERIF: wx.FONTFAMILY_ROMAN, fonts.MONO: wx.FONTFAMILY_TELETYPE}


class TextDialog(wx.Dialog):
    """Multi-line text entry. Enter adds a line; Ctrl+Enter (or OK) accepts.
    The entry previews the chosen font family / bold / italic."""

    def __init__(self, parent: wx.Window, initial: str, style: Style) -> None:
        super().__init__(parent, title="Edit text" if initial else "Add text",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._text = wx.TextCtrl(self, value=initial, style=wx.TE_MULTILINE, size=(420, 140))
        font = wx.Font(wx.FontInfo(14).Family(_WX_FAMILIES.get(style.font_family, wx.FONTFAMILY_SWISS))
                       .Bold(style.bold).Italic(style.italic))
        self._text.SetFont(font)
        hint = wx.StaticText(self, label="Ctrl+Enter to accept. Font, size and colour are set in the sidebar."
                                         + (" Empty text deletes it." if initial else ""))
        hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._text, 1, wx.EXPAND | wx.ALL, 12)
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT, 12)
        sizer.Add(self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizerAndFit(sizer)
        self.CentreOnParent()
        self._text.SetFocus()
        self._text.SetInsertionPointEnd()
        self._text.Bind(wx.EVT_KEY_DOWN, self._on_key)

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and event.ControlDown():
            self.EndModal(wx.ID_OK)
            return
        event.Skip()

    def value(self) -> str:
        return self._text.GetValue()


def ask_text(parent: wx.Window, initial: str, style: Style) -> Optional[str]:
    with TextDialog(parent, initial, style) as dialog:
        return dialog.value() if dialog.ShowModal() == wx.ID_OK else None


class ResizeDialog(wx.Dialog):
    """Image > Resize: pixels or percent, aspect ratio locked by default."""

    def __init__(self, parent: wx.Window, size: Tuple[int, int]) -> None:
        super().__init__(parent, title="Resize image")
        self._orig = size
        w, h = size
        self._width = wx.SpinCtrl(self, min=1, max=MAX_DIMENSION, initial=w)
        self._height = wx.SpinCtrl(self, min=1, max=MAX_DIMENSION, initial=h)
        self._percent = wx.SpinCtrlDouble(self, min=0.1, max=10000, initial=100, inc=5)
        self._percent.SetDigits(1)
        self._lock = wx.CheckBox(self, label="Keep aspect ratio")
        self._lock.SetValue(True)
        self._filter = wx.Choice(self, choices=[label for label, _ in RESAMPLE_FILTERS])
        self._filter.SetSelection(0)
        current = wx.StaticText(self, label=f"Current size: {w} × {h} px. Shapes are scaled along.")
        current.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=12)
        grid.AddGrowableCol(1)
        for label, control in (("Width (px)", self._width), ("Height (px)", self._height),
                               ("Percent", self._percent)):
            grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(control, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._lock)
        grid.Add(wx.StaticText(self, label="Quality"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._filter, 1, wx.EXPAND)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(current, 0, wx.ALL, 12)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        sizer.Add(self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizerAndFit(sizer)
        self.CentreOnParent()

        self._width.Bind(wx.EVT_SPINCTRL, lambda e: self._changed("w"))
        self._width.Bind(wx.EVT_TEXT, lambda e: self._changed("w"))
        self._height.Bind(wx.EVT_SPINCTRL, lambda e: self._changed("h"))
        self._height.Bind(wx.EVT_TEXT, lambda e: self._changed("h"))
        self._percent.Bind(wx.EVT_SPINCTRLDOUBLE, lambda e: self._changed("p"))
        self._percent.Bind(wx.EVT_TEXT, lambda e: self._changed("p"))
        self._updating = False

    def _changed(self, source: str) -> None:
        if getattr(self, "_updating", True):
            return
        ow, oh = self._orig
        self._updating = True
        try:
            if source == "p":
                factor = self._percent.GetValue() / 100
                self._width.SetValue(max(1, round(ow * factor)))
                self._height.SetValue(max(1, round(oh * factor)))
            elif source == "w":
                factor = self._width.GetValue() / ow
                if self._lock.GetValue():
                    self._height.SetValue(max(1, round(oh * factor)))
                self._percent.SetValue(factor * 100)
            else:
                factor = self._height.GetValue() / oh
                if self._lock.GetValue():
                    self._width.SetValue(max(1, round(ow * factor)))
                self._percent.SetValue(factor * 100)
        finally:
            self._updating = False

    def result(self):
        return self._width.GetValue(), self._height.GetValue(), RESAMPLE_FILTERS[self._filter.GetSelection()][1]
