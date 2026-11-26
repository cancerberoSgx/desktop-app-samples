"""Canvas tools as small wx-free state machines.

The canvas converts wx mouse events to image coordinates and calls
`press` / `drag` / `release` / `double_click` / `cursor_at` on the active
tool; everything else (what is selected, the current style, the document)
lives on the `Editor` passed in. Panning (Hand tool, middle button,
Space+drag) and zooming are handled by the canvas itself.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple, Type

from app.editor.shapes import (ArrowShape, BoxShape, EllipseShape, LineShape, RectShape, Shape, TextShape,
                               normalized, shape_from_style, snap_45)

if TYPE_CHECKING:
    from app.editor.editor import Editor

Rect = Tuple[float, float, float, float]

# Cursor names the canvas maps to wx stock cursors.
ARROW, CROSS, HAND, MOVE, IBEAM = "arrow", "cross", "hand", "move", "ibeam"
SIZE_NWSE, SIZE_NESW, SIZE_NS, SIZE_WE = "size_nwse", "size_nesw", "size_ns", "size_we"
_HANDLE_CURSORS = {"nw": SIZE_NWSE, "se": SIZE_NWSE, "ne": SIZE_NESW, "sw": SIZE_NESW,
                   "n": SIZE_NS, "s": SIZE_NS, "e": SIZE_WE, "w": SIZE_WE, "start": CROSS, "end": CROSS}


@dataclass(frozen=True)
class Mods:
    shift: bool = False
    ctrl: bool = False
    alt: bool = False


NO_MODS = Mods()


def handle_at(handles, x: float, y: float, reach: float) -> Optional[str]:
    for name, (hx, hy) in handles:
        if abs(hx - x) <= reach and abs(hy - y) <= reach:
            return name
    return None


def _intersects(a: Rect, b: Rect) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


class Tool:
    NAME = ""
    LABEL = ""
    KEY = ""        # single-letter shortcut
    HINT = ""       # status bar help
    PANS = False    # left-drag pans the view (Hand tool)

    def __init__(self, editor: "Editor") -> None:
        self.editor = editor

    @property
    def doc(self):
        return self.editor.doc

    def press(self, x: float, y: float, mods: Mods) -> None:
        pass

    def drag(self, x: float, y: float, mods: Mods) -> None:
        pass

    def release(self, x: float, y: float, mods: Mods) -> None:
        pass

    def double_click(self, x: float, y: float, mods: Mods) -> None:
        pass

    def cursor_at(self, x: float, y: float) -> str:
        return CROSS

    def key(self, name: str, mods: Mods) -> bool:
        """Handle a key ("enter", "escape"); True if consumed."""
        return False

    def cancel(self) -> None:
        """Abandon whatever is in progress (tool switch, undo, new document)."""

    def preview(self) -> Optional[Shape]:
        """A shape being drawn, rendered like the real ones."""
        return None


class SelectTool(Tool):
    NAME, LABEL, KEY = "select", "Select", "V"
    HINT = ("Click a shape to select it, drag to move (Shift: straight), drag a handle to resize. "
            "Drag on empty space to select several. Double-click text to edit it.")

    def __init__(self, editor: "Editor") -> None:
        super().__init__(editor)
        self._mode: Optional[str] = None   # "move" | "handle" | "marquee"
        self._handle = ""
        self._target: Optional[Shape] = None
        self._start = (0.0, 0.0)
        self._applied = (0.0, 0.0)
        self._changed = False
        self.marquee: Optional[Rect] = None

    def _single_handle(self, x: float, y: float) -> Optional[Tuple[Shape, str]]:
        selected = self.editor.selected_shapes()
        if len(selected) != 1:
            return None
        name = handle_at(selected[0].handles(), x, y, self.editor.handle_reach)
        return (selected[0], name) if name else None

    def press(self, x: float, y: float, mods: Mods) -> None:
        ed, doc = self.editor, self.doc
        if doc is None:
            return
        self._changed = False
        hit = self._single_handle(x, y)
        if hit:
            self._target, self._handle = hit
            doc.checkpoint("Resize")
            self._mode = "handle"
            return
        shape = doc.shape_at(x, y, ed.tolerance)
        if shape is None:
            if not mods.shift:
                ed.select([])
            self._mode, self._start, self.marquee = "marquee", (x, y), None
            return
        if mods.shift:
            ed.toggle_selected(shape.id)
            self._mode = None
            return
        if shape.id not in ed.selection:
            ed.select([shape.id])
        doc.checkpoint("Move")
        self._mode, self._start, self._applied = "move", (x, y), (0.0, 0.0)

    def drag(self, x: float, y: float, mods: Mods) -> None:
        ed = self.editor
        if self._mode == "move":
            dx, dy = round(x - self._start[0]), round(y - self._start[1])
            if mods.shift:
                dx, dy = (dx, 0) if abs(dx) >= abs(dy) else (0, dy)
            step = (dx - self._applied[0], dy - self._applied[1])
            if step != (0, 0):
                for shape in ed.selected_shapes():
                    shape.move(*step)
                self._applied = (dx, dy)
                self._changed = True
                ed.notify("document")
        elif self._mode == "handle" and self._target is not None:
            self._handle = self._target.drag_handle(self._handle, round(x), round(y), mods.shift)
            self._changed = True
            ed.notify("document")
        elif self._mode == "marquee":
            self.marquee = normalized(self._start[0], self._start[1], x, y)
            ed.notify("overlay")

    def release(self, x: float, y: float, mods: Mods) -> None:
        ed, doc = self.editor, self.doc
        if self._mode in ("move", "handle") and doc is not None:
            if self._changed:
                ed.notify("document")
            else:
                doc.cancel_checkpoint()
        elif self._mode == "marquee" and self.marquee is not None and doc is not None:
            picked = [s.id for s in doc.shapes if _intersects(s.bounds(), self.marquee)]
            ed.select(sorted(set(ed.selection) | set(picked)) if mods.shift else picked)
            self.marquee = None
            ed.notify("overlay")
        self._mode, self._target = None, None

    def double_click(self, x: float, y: float, mods: Mods) -> None:
        if self.doc is None:
            return
        shape = self.doc.shape_at(x, y, self.editor.tolerance)
        if isinstance(shape, TextShape):
            self.editor.edit_text(shape)

    def cursor_at(self, x: float, y: float) -> str:
        if self.doc is None:
            return ARROW
        hit = self._single_handle(x, y)
        if hit:
            return _HANDLE_CURSORS.get(hit[1], CROSS)
        return MOVE if self.doc.shape_at(x, y, self.editor.tolerance) else ARROW

    def cancel(self) -> None:
        if self._mode in ("move", "handle") and self.doc is not None and not self._changed:
            self.doc.cancel_checkpoint()
        self._mode, self._target, self.marquee = None, None, None


class HandTool(Tool):
    NAME, LABEL, KEY = "hand", "Pan", "H"
    HINT = "Drag to move around the image. Ctrl+wheel zooms; Space+drag or the middle button pans with any tool."
    PANS = True

    def cursor_at(self, x: float, y: float) -> str:
        return HAND


class ShapeTool(Tool):
    """Drag to draw a shape of SHAPE_CLASS with the current style."""
    SHAPE_CLASS: Type[Shape] = RectShape

    def __init__(self, editor: "Editor") -> None:
        super().__init__(editor)
        self._shape: Optional[Shape] = None
        self._start = (0.0, 0.0)

    def press(self, x: float, y: float, mods: Mods) -> None:
        if self.doc is None:
            return
        self._start = (round(x), round(y))
        sx, sy = self._start
        self._shape = shape_from_style(self.SHAPE_CLASS, self.editor.style, x0=sx, y0=sy, x1=sx, y1=sy)

    def drag(self, x: float, y: float, mods: Mods) -> None:
        shape = self._shape
        if shape is None:
            return
        sx, sy = self._start
        if isinstance(shape, LineShape):
            x, y = snap_45(sx, sy, x, y) if mods.shift else (x, y)
        elif mods.shift:  # square / circle
            side = max(abs(x - sx), abs(y - sy))
            x = sx + (side if x >= sx else -side)
            y = sy + (side if y >= sy else -side)
        shape.x1, shape.y1 = round(x), round(y)  # type: ignore[attr-defined]
        self.editor.notify("overlay")

    def release(self, x: float, y: float, mods: Mods) -> None:
        shape, self._shape = self._shape, None
        if shape is None or self.doc is None:
            return
        if shape.is_degenerate(self.editor.min_drag):
            self.editor.notify("overlay")
            return
        if isinstance(shape, BoxShape):
            shape.normalize()
        self.doc.add_shape(shape)
        self.editor.select([shape.id])
        self.editor.notify("document")

    def preview(self) -> Optional[Shape]:
        return self._shape

    def cancel(self) -> None:
        self._shape = None


class RectTool(ShapeTool):
    NAME, LABEL, KEY = "rect", "Rectangle", "R"
    HINT = "Drag to draw a rectangle (Shift: square)."
    SHAPE_CLASS = RectShape


class EllipseTool(ShapeTool):
    NAME, LABEL, KEY = "ellipse", "Ellipse", "E"
    HINT = "Drag to draw an ellipse (Shift: circle)."
    SHAPE_CLASS = EllipseShape


class LineTool(ShapeTool):
    NAME, LABEL, KEY = "line", "Line", "L"
    HINT = "Drag to draw a line (Shift: 45° steps)."
    SHAPE_CLASS = LineShape


class ArrowTool(ShapeTool):
    NAME, LABEL, KEY = "arrow", "Arrow", "A"
    HINT = "Drag from the tail to the tip to draw an arrow (Shift: 45° steps)."
    SHAPE_CLASS = ArrowShape


class TextTool(Tool):
    NAME, LABEL, KEY = "text", "Text", "T"
    HINT = "Click to add text, click existing text to edit it. Font, size and colour are in the sidebar."

    # Acts on release, not press: it opens a modal dialog, which must not
    # happen while the canvas holds the mouse capture.
    def release(self, x: float, y: float, mods: Mods) -> None:
        ed, doc = self.editor, self.doc
        if doc is None:
            return
        existing = doc.shape_at(x, y, ed.tolerance)
        if isinstance(existing, TextShape):
            ed.select([existing.id])
            ed.edit_text(existing)
            return
        text = ed.request_text("")
        if not text or not text.strip():
            return
        shape = shape_from_style(TextShape, ed.style, x=round(x), y=round(y), text=text)
        doc.add_shape(shape)
        ed.select([shape.id])
        ed.notify("document")

    def cursor_at(self, x: float, y: float) -> str:
        return IBEAM


class CropTool(Tool):
    NAME, LABEL, KEY = "crop", "Crop", "C"
    HINT = "Drag to choose the area to keep (Shift: square), adjust it with the handles, then press Enter. Esc clears it."

    def __init__(self, editor: "Editor") -> None:
        super().__init__(editor)
        self.rect: Optional[Tuple[int, int, int, int]] = None
        self._mode: Optional[str] = None   # "new" | "move" | "handle"
        self._handle = ""
        self._start = (0.0, 0.0)
        self._rect_at_press: Optional[Tuple[int, int, int, int]] = None

    def _box(self) -> Optional[RectShape]:
        return RectShape(x0=self.rect[0], y0=self.rect[1], x1=self.rect[2], y1=self.rect[3], width=0) \
            if self.rect else None

    def handles(self):
        box = self._box()
        return box.handles() if box else []

    def _set(self, rect: Rect) -> None:
        self.rect = self.doc.clamp_rect(rect) if self.doc is not None else None
        self.editor.notify("overlay")

    def press(self, x: float, y: float, mods: Mods) -> None:
        if self.doc is None:
            return
        self._start, self._rect_at_press = (x, y), self.rect
        name = handle_at(self.handles(), x, y, self.editor.handle_reach)
        if name:
            self._mode, self._handle = "handle", name
        elif self.rect and self.rect[0] <= x <= self.rect[2] and self.rect[1] <= y <= self.rect[3]:
            self._mode = "move"
        else:
            self._mode = "new"
            self.rect = None
            self.editor.notify("overlay")

    def drag(self, x: float, y: float, mods: Mods) -> None:
        sx, sy = self._start
        if self._mode == "new":
            if mods.shift:
                side = max(abs(x - sx), abs(y - sy))
                x, y = sx + (side if x >= sx else -side), sy + (side if y >= sy else -side)
            self._set((sx, sy, x, y))
        elif self._mode == "move" and self._rect_at_press:
            x0, y0, x1, y1 = self._rect_at_press
            w, h = self.doc.size
            dx = min(max(round(x - sx), -x0), w - x1)
            dy = min(max(round(y - sy), -y0), h - y1)
            self.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            self.editor.notify("overlay")
        elif self._mode == "handle":
            box = self._box()
            if box is not None:
                self._handle = box.drag_handle(self._handle, x, y, mods.shift)
                self._set(box.box())

    def release(self, x: float, y: float, mods: Mods) -> None:
        if self._mode == "new" and self.rect is not None:
            x0, y0, x1, y1 = self.rect
            if min(x1 - x0, y1 - y0) < 1 or max(x1 - x0, y1 - y0) < self.editor.min_drag:
                self.rect = None
                self.editor.notify("overlay")
        self._mode = None

    def cursor_at(self, x: float, y: float) -> str:
        name = handle_at(self.handles(), x, y, self.editor.handle_reach)
        if name:
            return _HANDLE_CURSORS[name]
        if self.rect and self.rect[0] <= x <= self.rect[2] and self.rect[1] <= y <= self.rect[3]:
            return MOVE
        return CROSS

    def key(self, name: str, mods: Mods) -> bool:
        if name == "enter" and self.rect:
            self.editor.apply_crop()
            return True
        if name == "escape" and self.rect:
            self.cancel()
            self.editor.notify("overlay")
            return True
        return False

    def cancel(self) -> None:
        self.rect, self._mode = None, None


TOOL_CLASSES: List[Type[Tool]] = [SelectTool, HandTool, CropTool, RectTool, EllipseTool, LineTool, ArrowTool,
                                  TextTool]
