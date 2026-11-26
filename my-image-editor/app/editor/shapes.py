"""Editable objects drawn over the image: rectangles, ellipses, lines, arrows
and text. wx-free and Pillow-free (except text measuring, via `fonts`).

All coordinates are image pixels (floats). Shapes stay objects - movable,
resizable, restylable - until the document is saved, which renders them into
a copy of the image (`render.composite`); the document itself keeps them.

Every shape has a stable `id` (kept across copies, so the UI can keep its
selection after undo/redo) and serializes to a plain dict (`to_dict` /
`shape_from_dict`) - the history snapshots use copies, and the dict form is
ready for a future "save project" feature.
"""
import copy
import itertools
import math
from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar, Dict, List, Optional, Tuple, Type

from app.editor import fonts

Color = Tuple[int, int, int, int]
Rect = Tuple[float, float, float, float]
Point = Tuple[float, float]

_ids = itertools.count(1)


def new_id() -> int:
    return next(_ids)


def _color(value, default: Optional[Color]) -> Optional[Color]:
    try:
        r, g, b, *rest = value
        a = rest[0] if rest else 255
        return tuple(max(0, min(255, int(c))) for c in (r, g, b, a))  # type: ignore[return-value]
    except (TypeError, ValueError):
        return default


@dataclass
class Style:
    """What new shapes get, and what the sidebar edits. One style serves every
    kind of shape; each kind uses the fields that apply to it (see
    `Shape.STYLE_FIELDS`)."""
    color: Color = (229, 57, 53, 255)
    fill: Optional[Color] = None
    width: float = 4.0
    font_family: str = fonts.DEFAULT_FAMILY
    font_size: int = 36
    bold: bool = False
    italic: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Style":
        """Tolerant: unknown keys are ignored, bad values fall back to defaults."""
        base = cls()
        if not isinstance(data, dict):
            return base
        try:
            width = float(data.get("width", base.width))
            size = int(data.get("font_size", base.font_size))
        except (TypeError, ValueError):
            width, size = base.width, base.font_size
        return cls(
            color=_color(data.get("color"), base.color) or base.color,
            fill=_color(data.get("fill"), None),
            width=min(max(width, 1.0), 200.0),
            font_family=fonts.normalize_family(str(data.get("font_family", base.font_family))),
            font_size=min(max(size, 4), 1000),
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
        )


@dataclass
class Shape:
    KIND: ClassVar[str] = ""
    LABEL: ClassVar[str] = ""
    STYLE_FIELDS: ClassVar[Tuple[str, ...]] = ("color",)

    color: Color = (229, 57, 53, 255)
    id: int = field(default_factory=new_id, compare=False)

    # -- geometry ---------------------------------------------------------
    def bounds(self) -> Rect:
        """Box covering everything the shape paints (stroke included)."""
        raise NotImplementedError

    def move(self, dx: float, dy: float) -> None:
        raise NotImplementedError

    def scale(self, sx: float, sy: float) -> None:
        """Image resized by (sx, sy): coordinates and sizes follow."""
        raise NotImplementedError

    def handles(self) -> List[Tuple[str, Point]]:
        """Named resize handles; dragging one calls `drag_handle`."""
        return []

    def drag_handle(self, name: str, x: float, y: float, constrain: bool = False) -> str:
        """Move handle `name` to (x, y). Returns the handle's name afterwards
        (it changes when a box is dragged inside out)."""
        return name

    def hit(self, x: float, y: float, tolerance: float) -> bool:
        raise NotImplementedError

    def is_degenerate(self, min_size: float) -> bool:
        """Too small to be what the user meant (e.g. a click without drag)."""
        return False

    # -- style ------------------------------------------------------------
    def apply_style(self, name: str, value) -> bool:
        if name not in self.STYLE_FIELDS:
            return False
        setattr(self, name, value)
        return True

    def style_values(self) -> Dict[str, object]:
        return {name: getattr(self, name) for name in self.STYLE_FIELDS}

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "id"}
        data["kind"] = self.KIND
        return data

    def key(self) -> str:
        """Identity of what the shape looks like (render cache key)."""
        return repr(sorted(self.to_dict().items()))

    def copy(self) -> "Shape":
        return copy.deepcopy(self)


def normalized(x0: float, y0: float, x1: float, y1: float) -> Rect:
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _grow(rect: Rect, by: float) -> Rect:
    return (rect[0] - by, rect[1] - by, rect[2] + by, rect[3] + by)


_FLIP_X = {"w": "e", "e": "w"}
_FLIP_Y = {"n": "s", "s": "n"}


@dataclass
class BoxShape(Shape):
    """Rectangle / ellipse: a box (x0, y0)-(x1, y1), kept normalized after
    every handle drag (x0 <= x1, y0 <= y1); a shape being drawn may be
    unnormalized until `normalize()`."""
    STYLE_FIELDS: ClassVar[Tuple[str, ...]] = ("color", "fill", "width")

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    width: float = 4.0
    fill: Optional[Color] = None

    def box(self) -> Rect:
        return normalized(self.x0, self.y0, self.x1, self.y1)

    def normalize(self) -> None:
        self.x0, self.y0, self.x1, self.y1 = self.box()

    def bounds(self) -> Rect:
        return _grow(self.box(), self.width / 2)

    def move(self, dx: float, dy: float) -> None:
        self.x0 += dx
        self.x1 += dx
        self.y0 += dy
        self.y1 += dy

    def scale(self, sx: float, sy: float) -> None:
        self.x0 *= sx
        self.x1 *= sx
        self.y0 *= sy
        self.y1 *= sy
        self.width = max(1.0, self.width * (sx + sy) / 2)

    def handles(self) -> List[Tuple[str, Point]]:
        x0, y0, x1, y1 = self.box()
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return [("nw", (x0, y0)), ("n", (mx, y0)), ("ne", (x1, y0)), ("e", (x1, my)),
                ("se", (x1, y1)), ("s", (mx, y1)), ("sw", (x0, y1)), ("w", (x0, my))]

    def drag_handle(self, name: str, x: float, y: float, constrain: bool = False) -> str:
        x0, y0, x1, y1 = self.box()
        if constrain and len(name) == 2:
            # Corner with Shift: keep it square, anchored at the opposite corner.
            ax = x1 if "w" in name else x0
            ay = y1 if "n" in name else y0
            side = max(abs(x - ax), abs(y - ay))
            x = ax + math.copysign(side, x - ax if x != ax else (-1 if "w" in name else 1))
            y = ay + math.copysign(side, y - ay if y != ay else (-1 if "n" in name else 1))
        if "w" in name:
            x0 = x
        if "e" in name:
            x1 = x
        if "n" in name:
            y0 = y
        if "s" in name:
            y1 = y
        flipped_x, flipped_y = x0 > x1, y0 > y1
        self.x0, self.y0, self.x1, self.y1 = normalized(x0, y0, x1, y1)
        return "".join((_FLIP_Y.get(c, c) if flipped_y and c in "ns" else
                        _FLIP_X.get(c, c) if flipped_x and c in "we" else c) for c in name)

    def is_degenerate(self, min_size: float) -> bool:
        x0, y0, x1, y1 = self.box()
        return (x1 - x0) < min_size or (y1 - y0) < min_size


@dataclass
class RectShape(BoxShape):
    KIND: ClassVar[str] = "rect"
    LABEL: ClassVar[str] = "Rectangle"

    def hit(self, x: float, y: float, tolerance: float) -> bool:
        x0, y0, x1, y1 = self.box()
        reach = tolerance + self.width / 2
        if self.fill is not None and x0 <= x <= x1 and y0 <= y <= y1:
            return True
        inside_outer = x0 - reach <= x <= x1 + reach and y0 - reach <= y <= y1 + reach
        inside_inner = x0 + reach < x < x1 - reach and y0 + reach < y < y1 - reach
        return inside_outer and not inside_inner


@dataclass
class EllipseShape(BoxShape):
    KIND: ClassVar[str] = "ellipse"
    LABEL: ClassVar[str] = "Ellipse"

    def hit(self, x: float, y: float, tolerance: float) -> bool:
        x0, y0, x1, y1 = self.box()
        rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
        if rx <= 0 or ry <= 0:
            return False
        dx, dy = x - (x0 + rx), y - (y0 + ry)
        r = math.hypot(dx / rx, dy / ry)
        if self.fill is not None and r <= 1:
            return True
        # |r - 1| scaled back to pixels along the smaller radius: close enough.
        return abs(r - 1) * min(rx, ry) <= tolerance + self.width / 2


def snap_45(ax: float, ay: float, x: float, y: float) -> Point:
    """(x, y) moved onto the nearest 45-degree ray from (ax, ay)."""
    dx, dy = x - ax, y - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return x, y
    angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    return ax + length * math.cos(angle), ay + length * math.sin(angle)


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


@dataclass
class LineShape(Shape):
    KIND: ClassVar[str] = "line"
    LABEL: ClassVar[str] = "Line"
    STYLE_FIELDS: ClassVar[Tuple[str, ...]] = ("color", "width")

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    width: float = 4.0

    def extra_reach(self) -> float:
        return 0.0

    def bounds(self) -> Rect:
        return _grow(normalized(self.x0, self.y0, self.x1, self.y1), self.width / 2 + self.extra_reach())

    def move(self, dx: float, dy: float) -> None:
        self.x0 += dx
        self.x1 += dx
        self.y0 += dy
        self.y1 += dy

    def scale(self, sx: float, sy: float) -> None:
        self.x0 *= sx
        self.x1 *= sx
        self.y0 *= sy
        self.y1 *= sy
        self.width = max(1.0, self.width * (sx + sy) / 2)

    def handles(self) -> List[Tuple[str, Point]]:
        return [("start", (self.x0, self.y0)), ("end", (self.x1, self.y1))]

    def drag_handle(self, name: str, x: float, y: float, constrain: bool = False) -> str:
        if name == "start":
            self.x0, self.y0 = snap_45(self.x1, self.y1, x, y) if constrain else (x, y)
        elif name == "end":
            self.x1, self.y1 = snap_45(self.x0, self.y0, x, y) if constrain else (x, y)
        return name

    def hit(self, x: float, y: float, tolerance: float) -> bool:
        return _segment_distance(x, y, self.x0, self.y0, self.x1, self.y1) <= tolerance + self.width / 2

    def is_degenerate(self, min_size: float) -> bool:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0) < min_size


@dataclass
class ArrowShape(LineShape):
    """A line with a filled head at (x1, y1)."""
    KIND: ClassVar[str] = "arrow"
    LABEL: ClassVar[str] = "Arrow"

    def head_size(self) -> Tuple[float, float]:
        """(length, half width) of the head, growing with the line width."""
        return 4 * self.width + 8, 2 * self.width + 4

    def extra_reach(self) -> float:
        return self.head_size()[1]

    def head_points(self) -> Tuple[Point, Point, Point, Point]:
        """(tip, left corner, right corner, base centre) - the line is drawn
        up to the base centre so its square end never pokes out of the tip."""
        length, half = self.head_size()
        dx, dy = self.x1 - self.x0, self.y1 - self.y0
        total = math.hypot(dx, dy) or 1.0
        ux, uy = dx / total, dy / total
        length = min(length, total)
        bx, by = self.x1 - ux * length, self.y1 - uy * length
        return ((self.x1, self.y1), (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half), (bx, by))


@dataclass
class TextShape(Shape):
    """Text whose line box starts at (x, y). `color` is the text colour."""
    KIND: ClassVar[str] = "text"
    LABEL: ClassVar[str] = "Text"
    STYLE_FIELDS: ClassVar[Tuple[str, ...]] = ("color", "font_family", "font_size", "bold", "italic")

    x: float = 0.0
    y: float = 0.0
    text: str = ""
    font_family: str = fonts.DEFAULT_FAMILY
    font_size: int = 36
    bold: bool = False
    italic: bool = False

    def bounds(self) -> Rect:
        _x0, _y0, x1, y1 = fonts.text_bbox(self.text, self.font_family, self.font_size, self.bold, self.italic)
        return (self.x, self.y, self.x + max(x1, 1), self.y + max(y1, self.font_size))

    def move(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy

    def scale(self, sx: float, sy: float) -> None:
        self.x *= sx
        self.y *= sy
        self.font_size = max(1, int(round(self.font_size * (sx + sy) / 2)))

    def hit(self, x: float, y: float, tolerance: float) -> bool:
        x0, y0, x1, y1 = _grow(self.bounds(), tolerance)
        return x0 <= x <= x1 and y0 <= y <= y1


SHAPE_TYPES: Dict[str, Type[Shape]] = {
    cls.KIND: cls for cls in (RectShape, EllipseShape, LineShape, ArrowShape, TextShape)
}


def shape_from_dict(data: dict) -> Shape:
    cls = SHAPE_TYPES[data["kind"]]
    names = {f.name for f in fields(cls)} - {"id"}
    values = {k: v for k, v in data.items() if k in names}
    for key in ("color", "fill"):
        if key in values and values[key] is not None:
            values[key] = tuple(values[key])
    return cls(**values)


def shape_from_style(cls: Type[Shape], style: Style, **geometry) -> Shape:
    """A new shape of `cls` with the style fields that apply to it."""
    values = {name: getattr(style, name) for name in cls.STYLE_FIELDS}
    values.update(geometry)
    return cls(**values)
