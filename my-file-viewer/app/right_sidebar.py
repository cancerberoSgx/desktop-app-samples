from typing import Callable, Optional

import wx

"""Right-hand sidebar - collapsible the same way (and for the same reason -
see CLAUDE.md's collapsible-sidebar convention) as FavoritesSidebar on the
left. Its first real section - Patterns, a glob filter over the folder
tree - lives here the same way FavoritesSidebar's favorites list is one
section among several; future features get added the same way, one
section at a time. Kept as its own class rather than folded into
FavoritesSidebar since its content has nothing to do with favorites - a
second, independent sidebar, not a second mode of the first one."""


class RightSidebar(wx.Panel):
    """Collapsible via the arrow button at the top: collapsed mode shrinks
    the bar down to a narrow strip (mirroring FavoritesSidebar's collapsed
    width), expanded mode shows its section(s). The toggle arrow points the
    opposite way from the left sidebar's, since collapsing/expanding
    happens towards the opposite screen edge - "»" (points right, towards
    this sidebar's own edge) while expanded, "«" (points left, back
    towards the main content) while collapsed.

    Patterns section: a `wx.TextCtrl` plus an Apply button (Enter in the
    box does the same thing, via `wx.TE_PROCESS_ENTER`) - `on_apply_pattern`
    fires with the box's current text on either. This control never
    decides what a pattern means or which rows it matches - same
    "report intent, let the caller render/apply" split as
    `FavoritesSidebar`'s `on_select`/`on_remove` - `MainFrame._on_apply_pattern`
    is what actually persists it and tells `FolderExplorerPage` to filter
    with it (see `FolderTreeCtrl`'s Glob pattern filter section). `Clear`
    empties the box and applies an empty pattern in one step, the same as
    submitting an emptied box would - a dedicated button only because
    clearing via the box means selecting all the text first otherwise.
    `set_pattern` prefills the box - called once at startup with whatever
    was persisted, and never fires `on_apply_pattern` itself, since a
    freshly-restored pattern is already applied by the time
    `FolderExplorerPage` is constructed (see `MainFrame.__init__`)."""

    EXPANDED_WIDTH = 220
    COLLAPSED_WIDTH = 48

    def __init__(
        self,
        parent: wx.Window,
        on_apply_pattern: Optional[Callable[[str], None]] = None,
        on_toggle_collapsed: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._on_apply_pattern = on_apply_pattern or (lambda pattern: None)
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

        self._patterns_label = wx.StaticText(self, label="PATTERNS")
        self._patterns_label.SetForegroundColour(wx.Colour(160, 168, 178))
        self._sizer.Add(self._patterns_label, 0, wx.LEFT | wx.TOP, 12)

        self._pattern_input = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._pattern_input.SetToolTip(
            "Glob pattern, e.g. *.py, src/**/*.py, node_modules/**/*\n"
            "A pattern with no '/' matches the name at any depth"
        )
        self._sizer.Add(self._pattern_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self._pattern_input.Bind(wx.EVT_TEXT_ENTER, self._on_apply_clicked)

        pattern_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self._pattern_apply_btn = wx.Button(self, label="Apply")
        self._pattern_apply_btn.Bind(wx.EVT_BUTTON, self._on_apply_clicked)
        pattern_buttons.Add(self._pattern_apply_btn, 0, wx.RIGHT, 4)
        self._pattern_clear_btn = wx.Button(self, label="Clear")
        self._pattern_clear_btn.Bind(wx.EVT_BUTTON, self._on_clear_clicked)
        pattern_buttons.Add(self._pattern_clear_btn, 0)
        self._sizer.Add(pattern_buttons, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self._pattern_buttons_sizer = pattern_buttons  # kept to Show()/Hide() as a group

        self._sizer.AddStretchSpacer()

        self.SetSizer(self._sizer)
        self.SetMinSize((self.EXPANDED_WIDTH, -1))

    # ------------------------------------------------------------------
    # Patterns
    # ------------------------------------------------------------------
    def set_pattern(self, pattern: str) -> None:
        """Prefills the box without applying it - ChangeValue, not
        SetValue, since there's no EVT_TEXT binding here to worry about
        either way (Apply is always explicit - Enter or the button - never
        a live-as-you-type filter like quick search's box), but ChangeValue
        is the correct one to reach for whenever an event firing would be
        purely incidental."""
        self._pattern_input.ChangeValue(pattern)

    def _on_apply_clicked(self, event: wx.CommandEvent) -> None:
        self._on_apply_pattern(self._pattern_input.GetValue())

    def _on_clear_clicked(self, event: wx.CommandEvent) -> None:
        self._pattern_input.ChangeValue("")
        self._on_apply_pattern("")

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
        self._patterns_label.Show(not collapsed)
        self._pattern_input.Show(not collapsed)
        self._sizer.Show(self._pattern_buttons_sizer, not collapsed, recursive=True)

        self.SetMinSize((self.COLLAPSED_WIDTH if collapsed else self.EXPANDED_WIDTH, -1))
        root_panel = self.GetParent()
        root_panel.Layout()
        root_panel.Refresh()
