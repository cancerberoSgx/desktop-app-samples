"""Drawing shapes with Pillow - the one renderer used both for the canvas and
for the saved file, so what you see is what gets saved (same fonts, same
anti-aliasing).

Each shape is rendered into its own small RGBA tile covering its bounds and
then alpha-composited. Pillow's ImageDraw doesn't anti-alias shapes, so
non-text tiles are drawn at SUPERSAMPLE x and scaled down (text is already
anti-aliased by FreeType). `clip` keeps tiles to the visible part: at high
zoom a shape can be far larger than the screen.

`ViewRenderer` produces what the canvas shows for one frame: the visible part
of the image at the current zoom with the shapes on top, over a checkerboard
when the image has transparency. It caches the zoomed-out base image and
every shape tile, so dragging one shape only re-renders that shape.
"""
import math
from collections import OrderedDict
from typing import Iterable, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from app.editor import fonts
from app.editor.shapes import ArrowShape, BoxShape, EllipseShape, LineShape, Shape, TextShape

SUPERSAMPLE = 3
# Cap on a supersampled tile (pixels) - huge shapes get less supersampling.
MAX_TILE_PIXELS = 24_000_000

IntBox = Tuple[int, int, int, int]


def has_alpha(image: Image.Image) -> bool:
    return image.mode == "RGBA" and image.getextrema()[3][0] < 255  # type: ignore[index]


def _tile_box(shape: Shape, scale: float, clip: Optional[IntBox]) -> Optional[IntBox]:
    x0, y0, x1, y1 = shape.bounds()
    box = (math.floor(x0 * scale) - 1, math.floor(y0 * scale) - 1,
           math.ceil(x1 * scale) + 1, math.ceil(y1 * scale) + 1)
    if clip is not None:
        box = (max(box[0], clip[0]), max(box[1], clip[1]), min(box[2], clip[2]), min(box[3], clip[3]))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def render_shape(shape: Shape, scale: float = 1.0,
                 clip: Optional[IntBox] = None) -> Optional[Tuple[Image.Image, Tuple[int, int]]]:
    """(RGBA tile, (left, top)) of `shape` drawn at `scale`, in the pixel grid
    of the image scaled by `scale`; None when nothing is inside `clip`."""
    box = _tile_box(shape, scale, clip)
    if box is None:
        return None
    left, top, right, bottom = box
    w, h = right - left, bottom - top

    ss = 1
    if not isinstance(shape, TextShape):
        ss = SUPERSAMPLE
        while ss > 1 and w * h * ss * ss > MAX_TILE_PIXELS:
            ss -= 1
    tile = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    k = scale * ss  # size of one image pixel in tile pixels

    def pt(x: float, y: float) -> Tuple[float, float]:
        return (x * scale - left) * ss, (y * scale - top) * ss

    if isinstance(shape, BoxShape):
        _draw_box(draw, shape, pt, k)
    elif isinstance(shape, LineShape):
        _draw_line(draw, shape, pt, k)
    elif isinstance(shape, TextShape):
        _draw_text(draw, shape, pt, scale)

    if ss > 1:
        tile = tile.resize((w, h), Image.Resampling.BOX)
    return tile, (left, top)


def _draw_box(draw: ImageDraw.ImageDraw, shape: BoxShape, pt, k: float) -> None:
    # The stroke is centred on the box edge (that's what bounds() assumes);
    # Pillow draws it inside the given box, so grow the box by half of it.
    half = shape.width / 2
    x0, y0, x1, y1 = shape.box()
    ax, ay = pt(x0 - half, y0 - half)
    bx, by = pt(x1 + half, y1 + half)
    stroke = max(1, int(round(shape.width * k)))
    method = draw.ellipse if isinstance(shape, EllipseShape) else draw.rectangle
    method([ax, ay, max(ax, bx - 1), max(ay, by - 1)], fill=shape.fill, outline=shape.color, width=stroke)


def _round_cap(draw: ImageDraw.ImageDraw, center: Tuple[float, float], diameter: float, color) -> None:
    r = diameter / 2
    if r >= 1:
        draw.ellipse([center[0] - r, center[1] - r, center[0] + r, center[1] + r], fill=color)


def _draw_line(draw: ImageDraw.ImageDraw, shape: LineShape, pt, k: float) -> None:
    stroke = max(1, int(round(shape.width * k)))
    start = pt(shape.x0, shape.y0)
    if isinstance(shape, ArrowShape):
        tip, left, right, base = (pt(*p) for p in shape.head_points())
        draw.line([start, base], fill=shape.color, width=stroke)
        _round_cap(draw, start, stroke, shape.color)
        draw.polygon([tip, left, right], fill=shape.color)
        return
    end = pt(shape.x1, shape.y1)
    draw.line([start, end], fill=shape.color, width=stroke)
    _round_cap(draw, start, stroke, shape.color)
    _round_cap(draw, end, stroke, shape.color)


def _draw_text(draw: ImageDraw.ImageDraw, shape: TextShape, pt, scale: float) -> None:
    size = max(1, int(round(shape.font_size * scale)))
    font = fonts.get_font(shape.font_family, size, shape.bold, shape.italic)
    draw.multiline_text(pt(shape.x, shape.y), shape.text, font=font, fill=shape.color,
                        spacing=fonts.line_spacing(size), anchor="la")


def composite(image: Image.Image, shapes: Iterable[Shape]) -> Image.Image:
    """A new RGBA image: `image` with `shapes` drawn on it (what gets saved)."""
    out = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
    clip = (0, 0, out.width, out.height)
    for shape in shapes:
        rendered = render_shape(shape, 1.0, clip)
        if rendered:
            tile, dest = rendered
            out.alpha_composite(tile, dest=dest)
    return out


CHECKER_SIZE = 8
CHECKER_LIGHT = (204, 204, 204)
CHECKER_DARK = (153, 153, 153)


def checkerboard(width: int, height: int, phase_x: int = 0, phase_y: int = 0) -> Image.Image:
    """RGBA checkerboard whose squares line up with the image's own pixel grid
    origin at (-phase_x, -phase_y)."""
    cell = CHECKER_SIZE
    ox, oy = phase_x % (2 * cell), phase_y % (2 * cell)
    cols, rows = (width + ox) // cell + 2, (height + oy) // cell + 2
    light, dark = bytes(CHECKER_LIGHT), bytes(CHECKER_DARK)
    data = b"".join((light if (c + r) % 2 == 0 else dark) for r in range(rows) for c in range(cols))
    small = Image.frombytes("RGB", (cols, rows), data)
    big = small.resize((cols * cell, rows * cell), Image.Resampling.NEAREST)
    return big.crop((ox, oy, ox + width, oy + height)).convert("RGBA")


class ViewRenderer:
    TILE_CACHE_SIZE = 256

    def __init__(self) -> None:
        self._scaled_src: Optional[Image.Image] = None
        self._scaled_scale = 0.0
        self._scaled: Optional[Image.Image] = None
        self._alpha_src: Optional[Image.Image] = None
        self._alpha = False
        self._tiles: "OrderedDict[tuple, Optional[tuple]]" = OrderedDict()

    def _scaled_image(self, image: Image.Image, scale: float) -> Image.Image:
        if self._scaled_src is not image or self._scaled_scale != scale or self._scaled is None:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            self._scaled = image.resize(size, Image.Resampling.BILINEAR, reducing_gap=2.0)
            self._scaled_src, self._scaled_scale = image, scale
        return self._scaled

    def _image_has_alpha(self, image: Image.Image) -> bool:
        if self._alpha_src is not image:
            self._alpha_src, self._alpha = image, has_alpha(image)
        return self._alpha

    def _tile(self, shape: Shape, scale: float, clip: IntBox):
        key = (shape.key(), scale, clip)
        if key in self._tiles:
            self._tiles.move_to_end(key)
            return self._tiles[key]
        rendered = render_shape(shape, scale, clip)
        self._tiles[key] = rendered
        if len(self._tiles) > self.TILE_CACHE_SIZE:
            self._tiles.popitem(last=False)
        return rendered

    def render(self, image: Image.Image, shapes: Sequence[Shape], scale: float,
               origin: Tuple[int, int], view_size: Tuple[int, int]) -> Optional[Tuple[Image.Image, Tuple[int, int]]]:
        """The visible part of the image (RGB) and where it goes, in device
        pixels. `origin` is where image pixel (0, 0) lands (integers, so the
        image grid and the tile grid line up); None if nothing is visible."""
        ox, oy = origin
        vw, vh = view_size
        x0, y0 = max(0, ox), max(0, oy)
        x1 = min(vw, ox + math.ceil(image.width * scale))
        y1 = min(vh, oy + math.ceil(image.height * scale))
        if x1 <= x0 or y1 <= y0:
            return None
        # The same region in the pixel grid of the image scaled by `scale`.
        rx0, ry0, rx1, ry1 = x0 - ox, y0 - oy, x1 - ox, y1 - oy
        size = (x1 - x0, y1 - y0)
        if scale < 1:
            base = self._scaled_image(image, scale).crop((rx0, ry0, rx1, ry1))
        else:
            box = (rx0 / scale, ry0 / scale, rx1 / scale, ry1 / scale)
            base = image.resize(size, Image.Resampling.NEAREST, box=box)
        if base.mode != "RGBA":
            base = base.convert("RGBA")

        clip = (rx0, ry0, rx1, ry1)
        for shape in shapes:
            rendered = self._tile(shape, scale, clip)
            if rendered:
                tile, (left, top) = rendered
                base.alpha_composite(tile, dest=(left - rx0, top - ry0))

        if self._image_has_alpha(image):
            board = checkerboard(size[0], size[1], rx0, ry0)
            board.alpha_composite(base)
            base = board
        return base.convert("RGB"), (x0, y0)
