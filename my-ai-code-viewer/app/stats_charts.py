"""Small custom-drawn charts for the Stats window (wx.GraphicsContext, no
plotting library - keeps the PyInstaller build small and lets the colours
follow the app theme like the conversation view does).

Every chart: thin marks with 4px rounded data-ends, a 2px gap between
adjacent marks, recessive grid, text in text colours (never series
colours), a tooltip per mark, and optional double-click -> `on_activate(i)`.
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import wx


@dataclass
class ChartTheme:
    surface: str
    text: str
    muted: str
    grid: str
    series: Tuple[str, ...]   # categorical, fixed order
    ramp: Tuple[str, ...]     # sequential, near-zero -> max


LIGHT = ChartTheme(
    surface="#ffffff", text="#1f2328", muted="#6e7781", grid="#e6e8eb",
    series=("#257de0", "#f47230", "#00b894", "#f2a600", "#ee7fac", "#008a17", "#6a4ce2", "#ec4647"),
    ramp=("#f2f4f7", "#cce3fd", "#9dc8f7", "#6aabf1", "#338ced", "#2370cd", "#1757ac", "#0e3d7e"),
)
DARK = ChartTheme(
    surface="#1e2228", text="#e6edf3", muted="#8b949e", grid="#30363d",
    series=("#338ced", "#e76624", "#00a888", "#d28e05", "#e24d8b", "#0aac23", "#9887ef", "#eb6263"),
    ramp=("#262c36", "#0e3d7e", "#0f4a96", "#1757ac", "#2370cd", "#338ced", "#6aabf1", "#9dc8f7"),
)

PAD = 12
RADIUS = 4
GAP = 2


def _colour(hex_str: str) -> wx.Colour:
    return wx.Colour(hex_str)


def nice_max(value: float) -> float:
    """Round an axis maximum up to 1/2/2.5/5 x 10^n."""
    if value <= 0:
        return 1
    exp = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def _bar_path(gc: wx.GraphicsContext, x: float, y: float, w: float, h: float, round_side: str):
    """Rectangle whose data-end (`right` or `top`) is rounded; the baseline
    end stays square."""
    r = min(RADIUS, w / 2, h / 2)
    path = gc.CreatePath()
    if round_side == "right":
        path.MoveToPoint(x, y)
        path.AddLineToPoint(x + w - r, y)
        path.AddArcToPoint(x + w, y, x + w, y + r, r)
        path.AddLineToPoint(x + w, y + h - r)
        path.AddArcToPoint(x + w, y + h, x + w - r, y + h, r)
        path.AddLineToPoint(x, y + h)
    else:  # top
        path.MoveToPoint(x, y + h)
        path.AddLineToPoint(x, y + r)
        path.AddArcToPoint(x, y, x + r, y, r)
        path.AddLineToPoint(x + w - r, y)
        path.AddArcToPoint(x + w, y, x + w, y + r, r)
        path.AddLineToPoint(x + w, y + h)
    path.CloseSubpath()
    return path


class Chart(wx.Panel):
    """Base: double-buffered painting, theme, per-mark tooltips."""

    def __init__(self, parent: wx.Window, theme: ChartTheme = LIGHT, min_height: int = 160):
        super().__init__(parent, style=wx.FULL_REPAINT_ON_RESIZE)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetMinSize((200, min_height))
        self.theme = theme
        self.on_activate: Optional[Callable[[int], None]] = None
        self._hits: List[Tuple[wx.Rect2D, str, int]] = []
        self._tip = ""
        self._font = self.GetFont()
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_LEAVE_WINDOW, lambda evt: self._set_tip(""))
        self.Bind(wx.EVT_LEFT_DCLICK, self._on_dclick)

    def set_theme(self, theme: ChartTheme) -> None:
        self.theme = theme
        self.Refresh()

    # subclasses
    def draw(self, gc: wx.GraphicsContext, w: int, h: int) -> None:
        raise NotImplementedError

    def _text(self, gc: wx.GraphicsContext, colour: str, bold: bool = False, smaller: bool = False):
        font = wx.Font(self._font)
        if smaller:
            font.SetFractionalPointSize(font.GetFractionalPointSize() * 0.9)
        if bold:
            font.SetWeight(wx.FONTWEIGHT_BOLD)
        gc.SetFont(font, _colour(colour))

    def _hit(self, x: float, y: float, w: float, h: float, tip: str, index: int) -> None:
        self._hits.append((wx.Rect2D(x, y, w, h), tip, index))

    def _on_paint(self, event: wx.PaintEvent) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(_colour(self.theme.surface)))
        dc.Clear()
        gc = wx.GraphicsContext.Create(dc)
        if gc is None:
            return
        self._hits = []
        w, h = self.GetClientSize()
        self.draw(gc, w, h)

    def _hit_at(self, pos: wx.Point) -> Optional[Tuple[wx.Rect2D, str, int]]:
        for hit in self._hits:
            if hit[0].Contains(wx.Point2D(pos.x, pos.y)):
                return hit
        return None

    def _on_motion(self, event: wx.MouseEvent) -> None:
        hit = self._hit_at(event.GetPosition())
        self._set_tip(hit[1] if hit else "")
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND if hit and self.on_activate else wx.CURSOR_ARROW))

    def _set_tip(self, tip: str) -> None:
        if tip != self._tip:
            self._tip = tip
            self.SetToolTip(tip or None)

    def _on_dclick(self, event: wx.MouseEvent) -> None:
        hit = self._hit_at(event.GetPosition())
        if hit and self.on_activate:
            self.on_activate(hit[2])

    def _empty(self, gc: wx.GraphicsContext, w: int, h: int) -> None:
        self._text(gc, self.theme.muted)
        tw, th = gc.GetTextExtent("No data in this range")
        gc.DrawText("No data in this range", (w - tw) / 2, (h - th) / 2)


@dataclass
class BarRow:
    label: str
    value: float
    value_text: str
    tooltip: str = ""
    colour_index: int = 0


class HBarChart(Chart):
    """Horizontal bars, one per row (label | bar | value). For rankings."""

    def __init__(self, parent: wx.Window, theme: ChartTheme = LIGHT, max_rows: int = 12):
        super().__init__(parent, theme)
        self.max_rows = max_rows
        self.rows: List[BarRow] = []

    def set_rows(self, rows: Sequence[BarRow]) -> None:
        self.rows = list(rows)[: self.max_rows]
        self.SetMinSize((200, max(80, 26 * len(self.rows) + 2 * PAD)))
        self.Refresh()

    def draw(self, gc, w, h):
        if not self.rows or all(r.value <= 0 for r in self.rows):
            return self._empty(gc, w, h)
        self._text(gc, self.theme.text)
        label_w = min(max(gc.GetTextExtent(r.label)[0] for r in self.rows) + 12, w * 0.35)
        value_w = max(gc.GetTextExtent(r.value_text)[0] for r in self.rows) + 10
        x0, x1 = PAD + label_w, w - PAD - value_w
        row_h = min(28, (h - 2 * PAD) / len(self.rows))
        bar_h = max(6, min(16, row_h - 10))
        top = max(r.value for r in self.rows)
        for i, r in enumerate(self.rows):
            y = PAD + i * row_h
            cy = y + row_h / 2
            label = r.label
            while label and gc.GetTextExtent(label)[0] > label_w - 12:
                label = label[:-2]
            if label != r.label:
                label = label[:-1] + "…"
            self._text(gc, self.theme.text)
            th = gc.GetTextExtent(label)[1]
            gc.DrawText(label, PAD, cy - th / 2)
            bw = max(0.0, (x1 - x0) * (r.value / top)) if top else 0
            if bw > 0:
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(_colour(self.theme.series[r.colour_index % len(self.theme.series)])))
                gc.FillPath(_bar_path(gc, x0, cy - bar_h / 2, max(bw, 2), bar_h, "right"))
            self._text(gc, self.theme.muted)
            gc.DrawText(r.value_text, x0 + bw + 6, cy - th / 2)
            self._hit(PAD, y, w - 2 * PAD, row_h, r.tooltip or f"{r.label}: {r.value_text}", i)


class ColumnChart(Chart):
    """Vertical (optionally stacked) columns over categories - histograms and
    "per day" series. `series` is [(name, values, colour_index)]."""

    def __init__(self, parent: wx.Window, theme: ChartTheme = LIGHT, value_name: str = ""):
        super().__init__(parent, theme, min_height=200)
        self.value_name = value_name
        self.categories: List[str] = []
        self.tick_labels: List[str] = []
        self.series: List[Tuple[str, List[float], int]] = []

    def set_data(self, categories: Sequence[str], series: Sequence[Tuple[str, Sequence[float], int]],
                 tick_labels: Optional[Sequence[str]] = None) -> None:
        self.categories = list(categories)
        self.tick_labels = list(tick_labels) if tick_labels is not None else list(categories)
        self.series = [(name, list(values), ci) for name, values, ci in series]
        self.Refresh()

    def draw(self, gc, w, h):
        n = len(self.categories)
        totals = [sum(s[1][i] for s in self.series) for i in range(n)]
        if not n or max(totals, default=0) <= 0:
            return self._empty(gc, w, h)
        self._text(gc, self.theme.muted, smaller=True)
        _, th = gc.GetTextExtent("0")
        legend_h = th + 8 if len(self.series) > 1 else 0
        top_val = nice_max(max(totals))
        axis_w = gc.GetTextExtent(_short(top_val))[0] + 8
        x0, x1 = PAD + axis_w, w - PAD
        y0, y1 = PAD + legend_h, h - PAD - th - 6   # plot top / baseline

        # legend (>= 2 series)
        if legend_h:
            lx = x0
            for name, _, ci in self.series:
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(_colour(self.theme.series[ci % len(self.theme.series)])))
                gc.DrawRoundedRectangle(lx, PAD + (th - 10) / 2, 10, 10, 2)
                self._text(gc, self.theme.text, smaller=True)
                gc.DrawText(name, lx + 14, PAD)
                lx += 14 + gc.GetTextExtent(name)[0] + 16

        # grid + y labels
        gc.SetPen(wx.Pen(_colour(self.theme.grid), 1))
        self._text(gc, self.theme.muted, smaller=True)
        for k in range(5):
            v = top_val * k / 4
            y = y1 - (y1 - y0) * k / 4
            gc.StrokeLine(x0, y, x1, y)
            label = _short(v)
            gc.DrawText(label, x0 - 6 - gc.GetTextExtent(label)[0], y - th / 2)

        slot = (x1 - x0) / n
        col_w = max(2.0, min(48.0, slot - GAP))
        step = max(1, int((gc.GetTextExtent("00-00")[0] + 12) // slot) + 1)
        for i in range(n):
            cx = x0 + slot * i + (slot - col_w) / 2
            y = y1
            segments = [(s, s[1][i]) for s in self.series if s[1][i] > 0]
            for j, ((name, _, ci), v) in enumerate(segments):
                sh = (y1 - y0) * v / top_val
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(_colour(self.theme.series[ci % len(self.theme.series)])))
                last = j == len(segments) - 1
                seg_h = sh - (GAP if not last and sh > GAP * 2 else 0)
                if last:
                    gc.FillPath(_bar_path(gc, cx, y - sh, col_w, max(seg_h, 1), "top"))
                else:
                    gc.DrawRectangle(cx, y - seg_h, col_w, max(seg_h, 1))
                y -= sh
            if i % step == 0 or n <= 12:
                self._text(gc, self.theme.muted, smaller=True)
                label = self.tick_labels[i]
                tw = gc.GetTextExtent(label)[0]
                gc.DrawText(label, x0 + slot * i + slot / 2 - tw / 2, y1 + 4)
            lines = [self.categories[i]] + [f"{name}: {_fmt(s[i])}" for name, s, _ in self.series if len(self.series) > 1]
            lines.append(f"Total{(' ' + self.value_name) if self.value_name else ''}: {_fmt(totals[i])}")
            self._hit(x0 + slot * i, y0, slot, y1 - y0, "\n".join(lines), i)
        gc.SetPen(wx.Pen(_colour(self.theme.muted), 1))
        gc.StrokeLine(x0, y1, x1, y1)


class Heatmap(Chart):
    """Rows x columns of cells on the sequential ramp (weekday x hour)."""

    def __init__(self, parent: wx.Window, theme: ChartTheme = LIGHT, value_name: str = "prompts"):
        super().__init__(parent, theme, min_height=200)
        self.value_name = value_name
        self.matrix: List[List[int]] = []
        self.row_labels: List[str] = []
        self.col_labels: List[str] = []

    def set_data(self, matrix: List[List[int]], row_labels: List[str], col_labels: List[str]) -> None:
        self.matrix, self.row_labels, self.col_labels = matrix, row_labels, col_labels
        self.Refresh()

    def draw(self, gc, w, h):
        if not self.matrix or max(max(r) for r in self.matrix) <= 0:
            return self._empty(gc, w, h)
        top = max(max(r) for r in self.matrix)
        self._text(gc, self.theme.muted, smaller=True)
        label_w = max(gc.GetTextExtent(l)[0] for l in self.row_labels) + 8
        _, th = gc.GetTextExtent("0")
        legend_h = th + 10
        rows, cols = len(self.matrix), len(self.matrix[0])
        x0, y0 = PAD + label_w, PAD
        # Cells fill the width; height is capped so they never get taller than wide.
        cell_w = (w - x0 - PAD) / cols
        cell_h = min(cell_w, (h - y0 - PAD - th - 6 - legend_h) / rows)
        for r in range(rows):
            gc.DrawText(self.row_labels[r], PAD, y0 + r * cell_h + (cell_h - th) / 2)
            for c in range(cols):
                v = self.matrix[r][c]
                level = 0 if v == 0 else 1 + int((len(self.theme.ramp) - 2) * (v / top) ** 0.7 + 0.5)
                level = min(level, len(self.theme.ramp) - 1)
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(_colour(self.theme.ramp[level])))
                x, y = x0 + c * cell_w, y0 + r * cell_h
                gc.DrawRoundedRectangle(x + 1, y + 1, cell_w - GAP, cell_h - GAP, 2)
                self._hit(x, y, cell_w, cell_h, f"{self.row_labels[r]} {self.col_labels[c]}:00 - "
                                            f"{v} {self.value_name}", r * cols + c)
        base = y0 + rows * cell_h + 4
        for c in range(0, cols, 3):
            tw = gc.GetTextExtent(self.col_labels[c])[0]
            gc.DrawText(self.col_labels[c], x0 + c * cell_w + (cell_w - tw) / 2, base)
        # legend: less -> more
        ly = base + th + 6
        gc.DrawText("Less", x0, ly)
        lx = x0 + gc.GetTextExtent("Less")[0] + 6
        for colour in self.theme.ramp:
            gc.SetBrush(wx.Brush(_colour(colour)))
            gc.DrawRoundedRectangle(lx, ly + (th - 10) / 2, 10, 10, 2)
            lx += 12
        gc.DrawText(f"More (max {top})", lx + 4, ly)


def _short(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:g}M"
    if v >= 1_000:
        return f"{v / 1_000:g}k"
    return f"{v:g}"


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def series_colour_index(key: str, order: Dict[str, int]) -> int:
    """Colour follows the entity: a fixed slot per provider, never its rank."""
    return order.get(key, len(order))
