from typing import Callable, Optional

import wx

"""Right-hand sidebar - collapsible the same way (and for the same reason -
see CLAUDE.md's collapsible-sidebar convention) as FavoritesSidebar on the
left, but with no content of its own yet: it's the anchor future features
will be added to as their own sections in `_sizer`, the same way
FavoritesSidebar's favorites list is one section among several. Kept as its
own class rather than folded into FavoritesSidebar since its eventual
content has nothing to do with favorites - a second, independent sidebar,
not a second mode of the first one."""


class RightSidebar(wx.Panel):
    """Collapsible via the arrow button at the top: collapsed mode shrinks
    the bar down to a narrow strip (mirroring FavoritesSidebar's collapsed
    width), expanded mode shows its (currently placeholder) content. The
    toggle arrow points the opposite way from the left sidebar's, since
    collapsing/expanding happens towards the opposite screen edge - "»"
    (points right, towards this sidebar's own edge) while expanded, "«"
    (points left, back towards the main content) while collapsed."""

    EXPANDED_WIDTH = 220
    COLLAPSED_WIDTH = 48

    def __init__(
        self,
        parent: wx.Window,
        on_toggle_collapsed: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._on_toggle_collapsed = on_toggle_collapsed
        self._collapsed = False

        self.SetBackgroundColour(wx.Colour(45, 51, 59))

        self._sizer = wx.BoxSizer(wx.VERTICAL)

        self._toggle_btn = wx.Button(self, label="»", size=(28, 28), style=wx.BORDER_NONE)
        self._toggle_btn.SetBackgroundColour(self.GetBackgroundColour())
        self._toggle_btn.SetForegroundColour(wx.Colour(230, 230, 230))
        self._toggle_btn.SetToolTip("Collapse sidebar")
        self._toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_clicked)
        # Aligned left (not right, like FavoritesSidebar's toggle) - that's
        # the edge bordering the main content on this, mirrored, side.
        self._sizer.Add(self._toggle_btn, 0, wx.ALIGN_LEFT | wx.LEFT | wx.TOP, 4)

        # Placeholder only - the first of what will become several feature
        # sections, each its own widget(s) added to self._sizer the same
        # way FavoritesSidebar's section label/list/empty-state are today.
        self._placeholder_label = wx.StaticText(self, label="More features\ncoming soon.")
        self._placeholder_label.SetForegroundColour(wx.Colour(150, 156, 164))
        self._sizer.Add(self._placeholder_label, 0, wx.ALL, 12)

        self._sizer.AddStretchSpacer()

        self.SetSizer(self._sizer)
        self.SetMinSize((self.EXPANDED_WIDTH, -1))

    # ------------------------------------------------------------------
    # Collapse
    # ------------------------------------------------------------------
    def _on_toggle_clicked(self, event: wx.CommandEvent) -> None:
        self.set_collapsed(not self._collapsed)
        if self._on_toggle_collapsed is not None:
            self._on_toggle_collapsed(self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed

        self._toggle_btn.SetLabel("«" if collapsed else "»")
        self._toggle_btn.SetToolTip("Expand sidebar" if collapsed else "Collapse sidebar")
        self._placeholder_label.Show(not collapsed)

        self.SetMinSize((self.COLLAPSED_WIDTH if collapsed else self.EXPANDED_WIDTH, -1))
        root_panel = self.GetParent()
        root_panel.Layout()
        root_panel.Refresh()
