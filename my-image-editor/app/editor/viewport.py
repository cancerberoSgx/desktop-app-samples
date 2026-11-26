"""Zoom and pan: the one place that converts between screen and image
coordinates.

Screen coordinates are the canvas's logical (DIP) pixels, i.e. what wx mouse
events report. `origin` is where image pixel (0, 0) is on screen; a point
maps as  screen = origin + image * zoom. The canvas renders in device pixels
(logical * content scale factor on HiDPI) - see `device_origin`.
"""
import math
from typing import Optional, Tuple

MIN_ZOOM = 0.01
MAX_ZOOM = 64.0
FIT_MARGIN = 16
# Zoom in/out walks these, like most image editors.
ZOOM_STEPS = (0.01, 0.02, 0.03, 0.05, 0.0625, 0.0833, 0.125, 0.1667, 0.25, 0.3333, 0.5, 0.6667,
              1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0)


class Viewport:
    def __init__(self) -> None:
        self.zoom = 1.0
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.image_size: Tuple[int, int] = (0, 0)
        self.view_size: Tuple[int, int] = (0, 0)

    # -------------------------------------------------------- conversions
    def to_image(self, sx: float, sy: float) -> Tuple[float, float]:
        return (sx - self.origin_x) / self.zoom, (sy - self.origin_y) / self.zoom

    def to_screen(self, x: float, y: float) -> Tuple[float, float]:
        return self.origin_x + x * self.zoom, self.origin_y + y * self.zoom

    def rect_to_screen(self, rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x0, y0 = self.to_screen(rect[0], rect[1])
        x1, y1 = self.to_screen(rect[2], rect[3])
        return x0, y0, x1, y1

    def to_image_distance(self, screen_px: float) -> float:
        """A screen distance (hit tolerance, handle size) in image pixels."""
        return screen_px / self.zoom

    def device_origin(self, scale_factor: float) -> Tuple[int, int]:
        """Image origin in device pixels, rounded so image pixels land on
        whole device pixels (the renderer requires integers)."""
        return int(round(self.origin_x * scale_factor)), int(round(self.origin_y * scale_factor))

    # -------------------------------------------------------- state
    def set_sizes(self, image_size: Tuple[int, int], view_size: Tuple[int, int]) -> None:
        self.image_size = image_size
        self.view_size = view_size
        self.clamp()

    def scaled_size(self) -> Tuple[float, float]:
        return self.image_size[0] * self.zoom, self.image_size[1] * self.zoom

    def clamp(self) -> None:
        """An axis where the image is smaller than the view is centred;
        otherwise the image can't be dragged so far that a gap opens up
        beyond its edge."""
        sw, sh = self.scaled_size()
        vw, vh = self.view_size
        self.origin_x = self._clamp_axis(self.origin_x, sw, vw)
        self.origin_y = self._clamp_axis(self.origin_y, sh, vh)

    @staticmethod
    def _clamp_axis(origin: float, scaled: float, view: float) -> float:
        if scaled <= view:
            return (view - scaled) / 2
        return min(0.0, max(view - scaled, origin))

    def pan(self, dx: float, dy: float) -> None:
        self.origin_x += dx
        self.origin_y += dy
        self.clamp()

    def set_zoom(self, zoom: float, anchor: Optional[Tuple[float, float]] = None) -> None:
        """Zoom keeping the image point under `anchor` (screen; default the
        view's centre) where it is."""
        zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom))
        ax, ay = anchor if anchor is not None else (self.view_size[0] / 2, self.view_size[1] / 2)
        ix, iy = self.to_image(ax, ay)
        self.zoom = zoom
        self.origin_x = ax - ix * zoom
        self.origin_y = ay - iy * zoom
        self.clamp()

    def zoom_by(self, factor: float, anchor: Optional[Tuple[float, float]] = None) -> None:
        self.set_zoom(self.zoom * factor, anchor)

    def step_zoom(self, direction: int, anchor: Optional[Tuple[float, float]] = None) -> None:
        """Next ZOOM_STEPS entry in `direction` (+1 in, -1 out)."""
        self.set_zoom(next_zoom_step(self.zoom, direction), anchor)

    def fit_zoom(self) -> float:
        iw, ih = self.image_size
        vw, vh = self.view_size
        if iw <= 0 or ih <= 0:
            return 1.0
        avail_w, avail_h = max(1, vw - 2 * FIT_MARGIN), max(1, vh - 2 * FIT_MARGIN)
        return min(MAX_ZOOM, max(MIN_ZOOM, min(avail_w / iw, avail_h / ih)))

    def fit(self, allow_upscale: bool = False) -> None:
        zoom = self.fit_zoom()
        self.zoom = zoom if allow_upscale else min(1.0, zoom)
        self.clamp()  # centres: the image now fits

    def actual_size(self) -> None:
        self.set_zoom(1.0)


def next_zoom_step(zoom: float, direction: int) -> float:
    eps = 1e-6
    if direction > 0:
        return next((z for z in ZOOM_STEPS if z > zoom + eps), ZOOM_STEPS[-1])
    return next((z for z in reversed(ZOOM_STEPS) if z < zoom - eps), ZOOM_STEPS[0])


def wheel_zoom_factor(rotation: int, delta: int) -> float:
    """Ctrl+wheel: 1.25x per notch, smooth for high-resolution wheels/pads."""
    notches = rotation / (delta or 120)
    return math.pow(1.25, notches)
