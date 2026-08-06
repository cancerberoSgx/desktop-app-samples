import math
from typing import List, Optional, Tuple

import wx

from .formatting import format_bytes

"""Hand-drawn pie chart + legend - no charting library dependency, same
"draw it ourselves" precedent as my-docker-viewer's sidebar.py hand-drawing
its own network icon (wx.ArtProvider has no pie-chart primitive to draw
with either). Colors, labeling, and the slice cap follow the dataviz
skill's rules rather than eyeballed choices - see CLAUDE.md for the
palette validator run this was checked against.
"""

# A pie chart specifically caps lower than the general categorical ladder
# (7-8 series) - past about 6 wedges they blur together at a glance, so
# the rest folds into a single gray "Other" wedge instead of a 7th hue.
MAX_REAL_SLICES = 6

# First 6 slots of the validated 8-hue categorical default, light/dark
# variants (see CLAUDE.md) - assigned in this FIXED order by rank (the
# biggest item is always slot 1/blue), never cycled, so color follows
# identity consistently rather than being regenerated per redraw.
_HUES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
_HUES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]
# "Other" is deliberately gray, not a 7th hue - it isn't a real identity,
# just a fold-in bucket, and gray reads as "the rest" at a glance.
_OTHER_LIGHT = "#767672"
_OTHER_DARK = "#a8a79e"

# Below this angular sweep, a slice's chord is too narrow to hold direct-
# label text without it spilling into its neighbors - skip the label
# (the legend still carries it) rather than clutter/overflow.
_MIN_LABEL_SWEEP = math.radians(25)


def _is_dark(colour: wx.Colour) -> bool:
    """Relative-luminance heuristic - picks which validated palette
    variant (light/dark) matches whatever surface this panel actually
    renders on. This app follows the OS/GTK theme rather than toggling
    its own, so the chart has to ask at draw time instead of assuming."""
    luminance = 0.2126 * colour.Red() + 0.7152 * colour.Green() + 0.0722 * colour.Blue()
    return luminance < 128


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _wedge_path(gc: "wx.GraphicsContext", cx: float, cy: float, radius: float, start: float, end: float):
    """Builds one pie wedge as a straight-line-segment polygon rather than
    via GraphicsPath.AddArc - AddArc's angle/clockwise convention is easy
    to get backwards in a y-down screen coordinate system, whereas this is
    unambiguous plain trigonometry: `start`/`end` are radians in the usual
    math sense (0 = +x/3-o'clock), and the caller (see `_build_slices`)
    always passes an increasing start->end pair, which sweeps visually
    clockwise on screen because y increases downward here."""
    path = gc.CreatePath()
    path.MoveToPoint(cx, cy)
    span = end - start
    steps = max(1, int(72 * span / (2 * math.pi)) + 1)  # ~72 segments per full circle - smooth enough at any normal size
    for i in range(steps + 1):
        t = start + span * i / steps
        path.AddLineToPoint(cx + radius * math.cos(t), cy + radius * math.sin(t))
    path.CloseSubpath()
    return path


class PieChartPanel(wx.Panel):
    """Part-to-whole pie + legend for a small set of (label, size_bytes)
    items. Ranks by size, assigns the fixed categorical hue order, folds
    anything past MAX_REAL_SLICES into a gray "Other" wedge, and labels
    only the single largest wedge directly (sparing labeling - see
    marks-and-anatomy.md) while the legend carries every item's exact
    number. Hovering a wedge highlights it and shows its value in a
    tooltip - the "ship a hover layer by default" rule for an interactive
    chart. Legend/label text always stays in ink tokens (the panel's own
    foreground colour), never colored by a slice's own hue - see
    marks-and-anatomy.md's "text never wears the data color".

    0 items renders a plain message; exactly 1 item (no "Other" bucket
    either) renders as plain text rather than a meaningless full circle -
    a one-slice "pie" has nothing to compare, the same reasoning
    anti-patterns.md gives for why a 2-slice pie should be a stat tile
    instead, just more so."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        # (label, size_bytes, is_other) - ranked, capped, "Other" folded.
        self._items: List[Tuple[str, int, bool]] = []
        # (label, size_bytes, colour, start_angle, end_angle), parallel to _items.
        self._slices: List[Tuple[str, int, wx.Colour, float, float]] = []
        self._hover_index: Optional[int] = None
        self._center: Tuple[float, float] = (0.0, 0.0)
        self._radius = 0.0

        self.SetMinSize((360, 260))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE, lambda evt: self.Refresh())
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)

    def set_items(self, items: List[Tuple[str, int]]) -> None:
        """`items`: (label, size_bytes) pairs in any order - ranked by size
        descending and capped to MAX_REAL_SLICES (+"Other") here, so every
        caller can just hand over its raw list. Zero-byte items are
        dropped outright (an unscanned entry has no meaningful slice)."""
        ranked = sorted((it for it in items if it[1] > 0), key=lambda it: it[1], reverse=True)
        head, tail = ranked[:MAX_REAL_SLICES], ranked[MAX_REAL_SLICES:]
        entries = [(label, size, False) for label, size in head]
        other_total = sum(size for _, size in tail)
        if other_total > 0:
            entries.append(("Other", other_total, True))
        self._items = entries
        self._hover_index = None
        self._build_slices()
        self.Refresh()

    def _build_slices(self) -> None:
        dark = _is_dark(self.GetBackgroundColour())
        hues = _HUES_DARK if dark else _HUES_LIGHT
        other_hue = _OTHER_DARK if dark else _OTHER_LIGHT
        total = sum(size for _, size, _ in self._items)
        self._slices = []
        if total <= 0:
            return
        angle = -math.pi / 2  # start at 12 o'clock
        hue_index = 0
        for label, size, is_other in self._items:
            sweep = 2 * math.pi * size / total
            colour = wx.Colour(other_hue if is_other else hues[hue_index % len(hues)])
            if not is_other:
                hue_index += 1
            self._slices.append((label, size, colour, angle, angle + sweep))
            angle += sweep

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def _on_paint(self, event: wx.PaintEvent) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        background = self.GetBackgroundColour()
        dc.SetBackground(wx.Brush(background))
        dc.Clear()
        gc = wx.GraphicsContext.Create(dc)
        if gc is not None:
            self._draw(gc, background)

    def _draw(self, gc: "wx.GraphicsContext", background: wx.Colour) -> None:
        ink = self.GetForegroundColour()
        width, height = self.GetClientSize()
        gc.SetFont(gc.CreateFont(self.GetFont(), ink))

        if not self._items:
            gc.DrawText("Nothing to chart yet.", 12, 12)
            return
        if len(self._items) == 1:
            label, size, _ = self._items[0]
            bold_font = wx.Font(self.GetFont())
            bold_font.MakeBold()
            gc.SetFont(gc.CreateFont(bold_font, ink))
            gc.DrawText(label, 12, 12)
            gc.SetFont(gc.CreateFont(self.GetFont(), ink))
            gc.DrawText(f"{format_bytes(size)} - the only item here, nothing to compare.", 12, 36)
            return

        legend_width = 190
        pie_area_width = max(width - legend_width, 60)
        self._center = (pie_area_width / 2, height / 2)
        self._radius = max(min(pie_area_width, height) / 2 - 16, 10)
        cx, cy = self._center
        total = sum(size for _, size, _ in self._items)

        for index, (label, size, colour, start, end) in enumerate(self._slices):
            radius = self._radius * 1.04 if index == self._hover_index else self._radius
            gc.SetBrush(gc.CreateBrush(wx.Brush(colour)))
            # A surface-color ring separates wedges - the spacer mechanism
            # (marks-and-anatomy.md), not a data-weight border.
            gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(background).Width(2)))
            gc.DrawPath(_wedge_path(gc, cx, cy, radius, start, end))

        self._draw_direct_label(gc, cx, cy)
        self._draw_legend(gc, pie_area_width + 16, 8, total, ink)

    def _draw_direct_label(self, gc: "wx.GraphicsContext", cx: float, cy: float) -> None:
        """Labels only the single largest wedge (sparing labeling - see
        marks-and-anatomy.md); skipped if even the biggest slice is too
        thin to hold text without spilling past its own wedge. Ink color
        is picked by the wedge's own fill luminance so it always clears
        contrast against it (the one case marks-and-anatomy.md carves out
        for text riding a colored fill)."""
        label, size, colour, start, end = max(self._slices, key=lambda s: s[1])
        if end - start < _MIN_LABEL_SWEEP:
            return
        total = sum(s for _, s, _ in self._items)
        mid = (start + end) / 2
        lx = cx + self._radius * 0.62 * math.cos(mid)
        ly = cy + self._radius * 0.62 * math.sin(mid)
        label_ink = wx.Colour(255, 255, 255) if _is_dark(colour) else wx.Colour(0, 0, 0)
        gc.SetFont(gc.CreateFont(self.GetFont(), label_ink))
        text = f"{_truncate(label, 16)} · {size / total * 100:.0f}%"
        text_width, text_height = gc.GetTextExtent(text)
        gc.DrawText(text, lx - text_width / 2, ly - text_height / 2)

    def _draw_legend(self, gc: "wx.GraphicsContext", x: float, y: float, total: int, ink: wx.Colour) -> None:
        """Always present for 2+ slices (the dependable identity channel -
        never rely on color-matching alone, per marks-and-anatomy.md).
        Text stays in the ink color throughout; only the swatch carries
        the slice's own hue."""
        row_height = 20
        gc.SetFont(gc.CreateFont(self.GetFont(), ink))
        for index, (label, size, colour, _start, _end) in enumerate(self._slices):
            row_y = y + index * row_height
            gc.SetBrush(gc.CreateBrush(wx.Brush(colour)))
            if index == self._hover_index:
                gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(ink).Width(1)))
            else:
                gc.SetPen(wx.TRANSPARENT_PEN)
            gc.DrawRectangle(x, row_y + 3, 12, 12)
            pct = size / total * 100 if total else 0.0
            gc.DrawText(f"{_truncate(label, 20)}  {format_bytes(size)} ({pct:.1f}%)", x + 18, row_y)

    # ------------------------------------------------------------------
    # Hover - highlight + tooltip
    # ------------------------------------------------------------------
    def _on_motion(self, event: wx.MouseEvent) -> None:
        if not self._slices:
            return
        x, y = event.GetPosition()
        cx, cy = self._center
        dx, dy = x - cx, y - cy
        if math.hypot(dx, dy) > self._radius * 1.1:
            self._set_hover(None)
            return
        theta = math.atan2(dy, dx) % (2 * math.pi)
        for index, (_label, _size, _colour, start, end) in enumerate(self._slices):
            s, e = start % (2 * math.pi), end % (2 * math.pi)
            hit = (s <= theta <= e) if s <= e else (theta >= s or theta <= e)
            if hit:
                self._set_hover(index)
                return
        self._set_hover(None)

    def _on_leave(self, event: wx.MouseEvent) -> None:
        self._set_hover(None)

    def _set_hover(self, index: Optional[int]) -> None:
        if index == self._hover_index:
            return
        self._hover_index = index
        if index is None:
            self.UnsetToolTip()
        else:
            label, size, _colour, _start, _end = self._slices[index]
            total = sum(s for _, s, _ in self._items)
            pct = size / total * 100 if total else 0.0
            self.SetToolTip(f"{label} — {format_bytes(size)} ({pct:.1f}%)")
        self.Refresh()
