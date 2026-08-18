from typing import Callable, List, Optional

import wx

# (label, wx.ArtProvider stock art id, page index)
SIDEBAR_ITEMS = [
    ("Profiles", wx.ART_HELP_SETTINGS),
    ("Documents", wx.ART_FILE_OPEN),
    ("Search", wx.ART_FIND),
    ("About", wx.ART_INFORMATION),
]


class Sidebar(wx.Panel):
    """Left-hand navigation bar with icon buttons that switch the main page.

    Collapsible via the arrow button at the top: collapsed mode shrinks the
    bar down to just the icons (labels/title hidden, tooltips take over) to
    save horizontal space, expanded mode is the original icon+label layout.
    """

    EXPANDED_WIDTH = 180
    COLLAPSED_WIDTH = 48

    def __init__(
        self,
        parent: wx.Window,
        on_select: Callable[[int], None],
        on_toggle_collapsed: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._on_select = on_select
        self._on_toggle_collapsed = on_toggle_collapsed
        self._buttons: List[wx.ToggleButton] = []
        self._labels: List[str] = [label for label, _art_id in SIDEBAR_ITEMS]
        self._collapsed = False

        self.SetBackgroundColour(wx.Colour(45, 51, 59))

        sizer = wx.BoxSizer(wx.VERTICAL)

        self._toggle_btn = wx.Button(
            self, label="«", size=(28, 28), style=wx.BORDER_NONE
        )
        self._toggle_btn.SetBackgroundColour(self.GetBackgroundColour())
        self._toggle_btn.SetForegroundColour(wx.Colour(230, 230, 230))
        self._toggle_btn.SetToolTip("Collapse sidebar")
        self._toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_clicked)
        sizer.Add(self._toggle_btn, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.TOP, 4)

        self._title = wx.StaticText(self, label="My Documents Viewer")
        self._title.SetForegroundColour(wx.Colour(230, 230, 230))
        font = self._title.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.MakeBold()
        self._title.SetFont(font)
        self._title.Wrap(Sidebar.EXPANDED_WIDTH - 16)
        sizer.Add(self._title, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 16)

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
        self.SetMinSize((self.EXPANDED_WIDTH, -1))

        self.select(0)

    def _make_button(self, label: str, art_id: str, page_index: Optional[int]) -> wx.ToggleButton:
        bitmap = wx.ArtProvider.GetBitmap(art_id, wx.ART_BUTTON, (20, 20))
        btn = wx.ToggleButton(self, label=f"  {label}")
        btn.SetBitmap(bitmap)
        btn.SetBitmapMargins(8, 4)
        btn.SetBackgroundColour(self.GetBackgroundColour())
        btn.SetForegroundColour(wx.Colour(230, 230, 230))
        btn.SetToolTip(label)

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

    def _on_toggle_clicked(self, event: wx.CommandEvent) -> None:
        self.set_collapsed(not self._collapsed)
        if self._on_toggle_collapsed is not None:
            self._on_toggle_collapsed(self._collapsed)

    def select(self, index: int) -> None:
        """Visually mark the button at `index` as active and release the rest."""
        for i, btn in enumerate(self._buttons):
            btn.SetValue(i == index)

    def set_collapsed(self, collapsed: bool) -> None:
        """Switch between the icon+label layout and the icon-only layout,
        then re-layout the frame so the page book reclaims/cedes the freed
        width."""
        self._collapsed = collapsed

        self._title.Show(not collapsed)
        self._toggle_btn.SetLabel("»" if collapsed else "«")
        self._toggle_btn.SetToolTip("Expand sidebar" if collapsed else "Collapse sidebar")

        for label, btn in zip(self._labels, self._buttons):
            btn.SetLabel("" if collapsed else f"  {label}")
        self._exit_btn.SetLabel("" if collapsed else "  Exit")

        self.SetMinSize((self.COLLAPSED_WIDTH if collapsed else self.EXPANDED_WIDTH, -1))
        # self.GetParent() is root_panel, whose sizer (root_sizer) owns both
        # the sidebar and the page book - re-laying it out redistributes the
        # width the sidebar just gave up/reclaimed to the book.
        root_panel = self.GetParent()
        root_panel.Layout()
        root_panel.Refresh()
