from typing import Callable, List, Optional

import wx

# (label, wx.ArtProvider stock art id, page index)
SIDEBAR_ITEMS = [
    ("Profiles", wx.ART_HELP_SETTINGS),
    ("Datasources", wx.ART_HARDDISK),
    ("About", wx.ART_INFORMATION),
]

_EXPANDED_WIDTH = 180
_COLLAPSED_WIDTH = 56


class Sidebar(wx.Panel):
    """Left-hand navigation bar with icon buttons that switch the main page.
    Collapsible via the arrow button next to the title, to save horizontal
    space - collapsed, buttons shrink to icon-only (label text hidden, full
    label kept as a tooltip)."""

    def __init__(self, parent: wx.Window, on_select: Callable[[int], None]) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._on_select = on_select
        self._buttons: List[wx.ToggleButton] = []
        self._collapsed = False

        self.SetBackgroundColour(wx.Colour(45, 51, 59))

        sizer = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        self._title = wx.StaticText(self, label="My Data Viewer")
        self._title.SetForegroundColour(wx.Colour(230, 230, 230))
        font = self._title.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.MakeBold()
        self._title.SetFont(font)
        header.Add(self._title, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 16)

        self._collapse_btn = wx.Button(self, label="«", size=(28, 28), style=wx.BORDER_NONE)
        self._collapse_btn.SetBackgroundColour(self.GetBackgroundColour())
        self._collapse_btn.SetForegroundColour(wx.Colour(230, 230, 230))
        self._collapse_btn.SetToolTip("Collapse sidebar")
        header.Add(self._collapse_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        sizer.Add(header, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 12)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.AddSpacer(8)

        for index, (label, art_id) in enumerate(SIDEBAR_ITEMS):
            btn = self._make_button(label, art_id, index)
            self._buttons.append(btn)
            sizer.Add(btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        sizer.AddStretchSpacer()

        self._exit_btn = self._make_button("Exit", wx.ART_QUIT, None)
        sizer.Add(self._exit_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.SetSizer(sizer)
        self.SetMinSize((_EXPANDED_WIDTH, -1))

        self._collapse_btn.Bind(wx.EVT_BUTTON, self._on_toggle_collapse)
        self.select(0)

    def _make_button(self, label: str, art_id: str, page_index: Optional[int]) -> wx.ToggleButton:
        bitmap = wx.ArtProvider.GetBitmap(art_id, wx.ART_BUTTON, (20, 20))
        btn = wx.ToggleButton(self, label=f"  {label}")
        btn.SetBitmap(bitmap)
        btn.SetBitmapMargins(8, 4)
        btn.SetBackgroundColour(self.GetBackgroundColour())
        btn.SetForegroundColour(wx.Colour(230, 230, 230))
        btn.nav_label = label  # stashed so collapse/expand can restore the text

        if page_index is None:
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_exit_clicked)
        else:
            btn.Bind(wx.EVT_TOGGLEBUTTON, lambda evt, i=page_index: self._on_button_clicked(i))

        return btn

    def _on_exit_clicked(self, event: wx.CommandEvent) -> None:
        self.GetTopLevelParent().Close()

    def _on_button_clicked(self, index: int) -> None:
        self.select(index)
        self._on_select(index)

    def _on_toggle_collapse(self, event: wx.CommandEvent) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """Switch between the full labeled sidebar and an icon-only strip.
        Each button's tooltip always shows its label, so navigation stays
        discoverable while collapsed."""
        self._collapsed = collapsed
        self._title.Show(not collapsed)
        self._collapse_btn.SetLabel("»" if collapsed else "«")
        self._collapse_btn.SetToolTip("Expand sidebar" if collapsed else "Collapse sidebar")
        for btn in self._buttons + [self._exit_btn]:
            btn.SetLabel("" if collapsed else f"  {btn.nav_label}")
            btn.SetToolTip(btn.nav_label if collapsed else "")
        self.SetMinSize((_COLLAPSED_WIDTH if collapsed else _EXPANDED_WIDTH, -1))
        self.Layout()
        self.GetParent().Layout()

    def select(self, index: int) -> None:
        """Visually mark the button at `index` as active and release the rest."""
        for i, btn in enumerate(self._buttons):
            btn.SetValue(i == index)
