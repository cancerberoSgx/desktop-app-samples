"""Helpers shared by op modules."""

from __future__ import annotations

import cv2
import numpy as np

NAMED_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "pink": (255, 192, 203),
    "lime": (50, 205, 50),
}

COLOR_HELP = "Color as '#RRGGBB', '#RRGGBBAA', a name (red, lime, ...) or 'R,G,B'. RGB order, not BGR."


def channels(img: np.ndarray) -> int:
    return 1 if img.ndim == 2 else img.shape[2]


def parse_color(color: str, n_channels: int = 3) -> tuple:
    """Parse a user-facing RGB(A) color and return an OpenCV scalar in BGR(A)
    order, sized for an image with ``n_channels`` channels."""
    c = color.strip().lower()
    alpha = 255
    if c in NAMED_COLORS:
        r, g, b = NAMED_COLORS[c]
    elif c.startswith("#") and len(c) in (7, 9):
        r, g, b = (int(c[i : i + 2], 16) for i in (1, 3, 5))
        if len(c) == 9:
            alpha = int(c[7:9], 16)
    elif "," in c:
        vals = [int(float(v)) for v in c.split(",")]
        if len(vals) not in (3, 4):
            raise ValueError(f"Bad color '{color}'. {COLOR_HELP}")
        r, g, b = vals[:3]
        if len(vals) == 4:
            alpha = vals[3]
    elif c.isdigit():
        r = g = b = int(c)
    else:
        raise ValueError(f"Bad color '{color}'. {COLOR_HELP}")
    if n_channels == 1:
        return (int(round(0.299 * r + 0.587 * g + 0.114 * b)),)
    if n_channels == 4:
        return (b, g, r, alpha)
    return (b, g, r)


def is_gray_color(color: str) -> bool:
    b, g, r = parse_color(color, 3)
    return b == g == r


def to_gray(img: np.ndarray) -> np.ndarray:
    ch = channels(img)
    if ch == 1:
        return img if img.ndim == 2 else img[:, :, 0]
    return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY if ch == 4 else cv2.COLOR_BGR2GRAY)


def to_bgr(img: np.ndarray) -> np.ndarray:
    ch = channels(img)
    if ch == 3:
        return img
    if ch == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return cv2.cvtColor(img if img.ndim == 2 else img[:, :, 0], cv2.COLOR_GRAY2BGR)


def to_uint8(img: np.ndarray) -> np.ndarray:
    """Map any dtype to uint8 for display: uint16 is scaled, bool/labels and
    float images are min-max normalised (float in [0,1] is scaled by 255)."""
    if img.dtype == np.uint8:
        return img
    if img.dtype == np.bool_:
        return img.astype(np.uint8) * 255
    if img.dtype == np.uint16:
        return (img / 257).astype(np.uint8)
    f = img.astype(np.float32)
    lo, hi = float(np.nanmin(f)), float(np.nanmax(f))
    if np.issubdtype(img.dtype, np.floating) and lo >= 0 and hi <= 1:
        return np.clip(f * 255, 0, 255).astype(np.uint8)
    if hi - lo < 1e-12:
        return np.zeros(img.shape, np.uint8)
    return ((f - lo) * (255.0 / (hi - lo))).astype(np.uint8)


def to_uint8_gray(img: np.ndarray) -> np.ndarray:
    return to_uint8(to_gray(img))


def match_to(img: np.ndarray, ref: np.ndarray, resize: bool = True) -> np.ndarray:
    """Make ``img`` compatible with ``ref`` (size, channel count, dtype)."""
    if resize and img.shape[:2] != ref.shape[:2]:
        img = cv2.resize(img, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_LINEAR)
    cr, ci = channels(ref), channels(img)
    if ci != cr:
        if cr == 1:
            img = to_gray(img)
        elif cr == 3:
            img = to_bgr(img)
        else:
            img = cv2.cvtColor(to_bgr(img), cv2.COLOR_BGR2BGRA)
    if img.dtype != ref.dtype:
        img = img.astype(ref.dtype)
    return img


def odd(k: int) -> int:
    k = max(1, int(k))
    return k if k % 2 else k + 1


def ensure_color_for(img: np.ndarray, color: str) -> np.ndarray:
    """Copy ``img`` for drawing, promoting gray to BGR when a non-gray color is used."""
    if channels(img) == 1 and not is_gray_color(color):
        return to_bgr(img).copy()
    return img.copy()


def interp(name: str) -> int:
    return {
        "nearest": cv2.INTER_NEAREST,
        "linear": cv2.INTER_LINEAR,
        "cubic": cv2.INTER_CUBIC,
        "area": cv2.INTER_AREA,
        "lanczos": cv2.INTER_LANCZOS4,
    }[name]


def border(name: str) -> int:
    return {
        "constant": cv2.BORDER_CONSTANT,
        "reflect": cv2.BORDER_REFLECT_101,
        "replicate": cv2.BORDER_REPLICATE,
        "wrap": cv2.BORDER_WRAP,
    }[name]
