from typing import Callable, List, Optional

import wx

# (label, wx.ArtProvider stock art id, page index)
SIDEBAR_ITEMS = [
    ("Containers", wx.ART_LIST_VIEW),
    ("Containers Disk", wx.ART_HARDDISK),
    ("Images", wx.ART_CDROM),
    ("Volumes", wx.ART_REMOVABLE),
    ("Networks", wx.ART_HELP_BROWSER),
    ("About", wx.ART_INFORMATION),
]

_BG_COLOUR = wx.Colour(45, 51, 59)
# A toggle button's native "pressed" look is what select() originally relied
# on alone to show the current page - measured to render with no visible
# difference at all against this flat, custom-coloured dark sidebar (GTK's
# theme engine has nothing left to draw once the background is overridden
# to match). Explicit colour/weight changes below are what's actually
# visible, on any platform/theme.
_ACTIVE_BG_COLOUR = wx.Colour(64, 96, 145)
_FG_COLOUR = wx.Colour(230, 230, 230)
_ACTIVE_FG_COLOUR = wx.Colour(255, 255, 255)


class Sidebar(wx.Panel):
    """Left-hand navigation bar with icon buttons that switch the main page."""

    def __init__(self, parent: wx.Window, on_select: Callable[[int], None]) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._on_select = on_select
        self._buttons: List[wx.ToggleButton] = []

        self.SetBackgroundColour(_BG_COLOUR)

        sizer = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="My Docker Viewer")
        title.SetForegroundColour(wx.Colour(230, 230, 230))
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.MakeBold()
        title.SetFont(font)
        sizer.Add(title, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 16)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sizer.AddSpacer(8)

        for index, (label, art_id) in enumerate(SIDEBAR_ITEMS):
            btn = self._make_button(label, art_id, index)
            self._buttons.append(btn)
            sizer.Add(btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        sizer.AddStretchSpacer()

        exit_btn = self._make_button("Exit", wx.ART_QUIT, None)
        sizer.Add(exit_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.SetSizer(sizer)
        self.SetMinSize((180, -1))

        self.select(0)

    def _make_button(self, label: str, art_id: str, page_index: Optional[int]) -> wx.ToggleButton:
        bitmap = wx.ArtProvider.GetBitmap(art_id, wx.ART_BUTTON, (20, 20))
        btn = wx.ToggleButton(self, label=f"  {label}")
        btn.SetBitmap(bitmap)
        btn.SetBitmapMargins(8, 4)
        self._set_button_active(btn, False)

        if page_index is None:
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_exit_clicked)
        else:
            btn.Bind(wx.EVT_TOGGLEBUTTON, lambda evt, i=page_index: self._on_button_clicked(i))

        return btn

    @staticmethod
    def _set_button_active(btn: wx.ToggleButton, active: bool) -> None:
        btn.SetBackgroundColour(_ACTIVE_BG_COLOUR if active else _BG_COLOUR)
        btn.SetForegroundColour(_ACTIVE_FG_COLOUR if active else _FG_COLOUR)
        font = btn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD if active else wx.FONTWEIGHT_NORMAL)
        btn.SetFont(font)
        btn.Refresh()

    def _on_exit_clicked(self, event: wx.CommandEvent) -> None:
        self.GetTopLevelParent().Close()

    def _on_button_clicked(self, index: int) -> None:
        self.select(index)
        self._on_select(index)

    def select(self, index: int) -> None:
        """Visually mark the button at `index` as the current page and
        release the rest - background colour and bold weight, not just
        SetValue()'s native toggle state, since that alone doesn't render
        as any visible difference against this sidebar's flat background."""
        for i, btn in enumerate(self._buttons):
            is_active = i == index
            btn.SetValue(is_active)
            self._set_button_active(btn, is_active)
