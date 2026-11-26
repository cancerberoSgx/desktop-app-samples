"""Color spaces, channels, tone and histograms."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import channels, to_gray

Space = Literal["bgr", "rgb", "gray", "hsv", "hls", "lab", "luv", "ycrcb", "xyz", "bgra", "rgba"]


@op("color")
def convert_color(image: Img, to: Space, source: Literal["auto"] | Space = "auto") -> Result:
    """Convert between color spaces. Images are BGR by default (OpenCV convention).

    source='auto' infers gray/bgr/bgra from the channel count; set it explicitly when converting
    *from* hsv/lab/etc. back to bgr. HSV uses OpenCV's 8-bit ranges: H 0-180, S and V 0-255.
    """
    src = source
    if src == "auto":
        src = {1: "gray", 3: "bgr", 4: "bgra"}[channels(image)]
    if src == to:
        return Result([image.copy()], {"note": "already in that color space"})
    code_name = f"COLOR_{src.upper()}2{to.upper()}"
    code = getattr(cv2, code_name, None)
    if code is None:
        # go through BGR for pairs OpenCV doesn't convert directly
        a = getattr(cv2, f"COLOR_{src.upper()}2BGR", None) if src != "bgr" else None
        b = getattr(cv2, f"COLOR_BGR2{to.upper()}", None) if to != "bgr" else None
        if (src != "bgr" and a is None) or (to != "bgr" and b is None):
            raise ValueError(f"No conversion from {src} to {to}.")
        out = cv2.cvtColor(image, a) if a is not None else image
        out = cv2.cvtColor(out, b) if b is not None else out
        return Result([out], {"conversion": f"{src}->bgr->{to}"})
    return Result([cv2.cvtColor(image, code)], {"conversion": code_name})


@op("color")
def split_channels(image: Img) -> Result:
    """Split a multi-channel image into one single-channel image per channel (B, G, R[, A] order for BGR)."""
    if channels(image) == 1:
        raise ValueError("Image already has a single channel.")
    return Result(list(cv2.split(image)))


@op("color")
def merge_channels(channels: list[Img]) -> Result:
    """Merge single-channel images (same size and dtype) into one multi-channel image, in the given order."""
    return Result([cv2.merge(channels)])


@op("color")
def brightness_contrast(image: Img, contrast: float = 1.0, brightness: float = 0.0) -> Result:
    """Linear adjustment: out = image * contrast + brightness (saturating for 8-bit)."""
    if image.dtype == np.uint8:
        out = cv2.convertScaleAbs(image, alpha=contrast, beta=brightness)
    else:
        out = (image.astype(np.float32) * contrast + brightness).astype(image.dtype)
    return Result([out])


@op("color")
def gamma_correction(image: Img, gamma: float = 1.0) -> Result:
    """Apply gamma correction; gamma < 1 brightens shadows, > 1 darkens."""
    if gamma <= 0:
        raise ValueError("gamma must be > 0")
    if image.dtype == np.uint8:
        lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
        return Result([cv2.LUT(image, lut)])
    maxv = 65535.0 if image.dtype == np.uint16 else max(float(image.max()), 1e-9)
    out = (np.power(image.astype(np.float32) / maxv, gamma) * maxv).astype(image.dtype)
    return Result([out])


@op("color")
def equalize_histogram(
    image: Img, method: Literal["global", "clahe"] = "clahe", clip_limit: float = 2.0, tile_size: int = 8
) -> Result:
    """Improve contrast via histogram equalization. Color images are equalized on the L channel (LAB)
    so hues are preserved. CLAHE (adaptive) usually looks better than global equalization."""

    def eq(ch: np.ndarray) -> np.ndarray:
        if method == "global":
            return cv2.equalizeHist(ch)
        return cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size)).apply(ch)

    if image.dtype != np.uint8 and method == "global":
        raise ValueError("global equalization needs an 8-bit image")
    if channels(image) == 1:
        return Result([eq(to_gray(image))])
    bgr = image[:, :, :3]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = eq(lab[:, :, 0])
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    if channels(image) == 4:
        out = np.dstack([out, image[:, :, 3]])
    return Result([out])


@op("color")
def histogram(image: Img, bins: int = 32, mask: Img | None = None) -> Result:
    """Compute per-channel histograms (no image output). Returns counts per bin plus mean/median."""
    chans = cv2.split(image) if channels(image) > 1 else [to_gray(image)]
    names = ["b", "g", "r", "a"] if len(chans) > 1 else ["gray"]
    hi = 256 if image.dtype == np.uint8 else (65536 if image.dtype == np.uint16 else float(image.max()) + 1e-6)
    m = to_gray(mask).astype(np.uint8) if mask is not None else None
    out = {}
    for name, ch in zip(names, chans):
        h = cv2.calcHist([ch], [0], m, [bins], [0, hi]).flatten()
        vals = ch[m > 0] if m is not None else ch
        out[name] = {
            "counts": [int(x) for x in h],
            "mean": round(float(vals.mean()), 3) if vals.size else None,
            "median": float(np.median(vals)) if vals.size else None,
        }
    return Result([], {"bins": bins, "range": [0, hi], "channels": out})


@op("color")
def in_range(
    image: Img,
    lower: list[float],
    upper: list[float],
    space: Literal["as_is", "hsv", "lab", "rgb"] = "as_is",
) -> Result:
    """Binary mask (255 where every channel is within [lower, upper]). Use space='hsv' to select colors
    by hue on a BGR image (H 0-180, S/V 0-255). Red wraps around H=0/180: combine two masks with bitwise."""
    src = image
    if space != "as_is":
        src = cv2.cvtColor(
            image[:, :, :3], {"hsv": cv2.COLOR_BGR2HSV, "lab": cv2.COLOR_BGR2LAB, "rgb": cv2.COLOR_BGR2RGB}[space]
        )
    if len(lower) != channels(src) or len(upper) != channels(src):
        raise ValueError(f"lower/upper need {channels(src)} values for this image")
    mask = cv2.inRange(src, np.array(lower, dtype=src.dtype), np.array(upper, dtype=src.dtype))
    return Result([mask], {"selected_ratio": round(float(np.count_nonzero(mask)) / mask.size, 4)})


@op("color")
def invert(image: Img) -> Result:
    """Invert an image (negative). Alpha, if present, is preserved."""
    if channels(image) == 4:
        out = image.copy()
        out[:, :, :3] = cv2.bitwise_not(image[:, :, :3]) if image.dtype != np.float32 else 1.0 - image[:, :, :3]
        return Result([out])
    if np.issubdtype(image.dtype, np.floating):
        return Result([float(image.max()) - image])
    return Result([cv2.bitwise_not(image)])


@op("color")
def normalize(
    image: Img,
    min_value: float = 0,
    max_value: float = 255,
    dtype: Literal["uint8", "uint16", "float32"] = "uint8",
) -> Result:
    """Min-max stretch the value range to [min_value, max_value] and convert dtype. Use it to turn
    float/label/distance maps into viewable 8-bit images."""
    ddepth = {"uint8": cv2.CV_8U, "uint16": cv2.CV_16U, "float32": cv2.CV_32F}[dtype]
    out = cv2.normalize(image, None, min_value, max_value, cv2.NORM_MINMAX, dtype=ddepth)
    return Result([out])
