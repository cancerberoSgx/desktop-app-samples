"""Global and adaptive thresholding."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import odd, to_gray, to_uint8_gray

_TYPES = {
    "binary": cv2.THRESH_BINARY,
    "binary_inv": cv2.THRESH_BINARY_INV,
    "trunc": cv2.THRESH_TRUNC,
    "tozero": cv2.THRESH_TOZERO,
    "tozero_inv": cv2.THRESH_TOZERO_INV,
}


@op("threshold")
def threshold(
    image: Img,
    value: float = 127,
    max_value: float = 255,
    type: Literal["binary", "binary_inv", "trunc", "tozero", "tozero_inv"] = "binary",
    method: Literal["fixed", "otsu", "triangle"] = "fixed",
) -> Result:
    """Global threshold of a grayscale version of the image. method 'otsu'/'triangle' picks the
    threshold automatically (8-bit); the threshold actually used is returned."""
    flags = _TYPES[type]
    if method == "fixed":
        src = to_gray(image)
    else:
        src = to_uint8_gray(image)
        flags |= cv2.THRESH_OTSU if method == "otsu" else cv2.THRESH_TRIANGLE
    used, out = cv2.threshold(src, value, max_value, flags)
    return Result(
        [out], {"threshold": float(used), "foreground_ratio": round(float(np.count_nonzero(out)) / out.size, 4)}
    )


@op("threshold")
def adaptive_threshold(
    image: Img,
    method: Literal["mean", "gaussian"] = "gaussian",
    block_size: int = 11,
    c: float = 2.0,
    invert: bool = False,
    max_value: float = 255,
) -> Result:
    """Local (adaptive) threshold: each pixel is compared with the mean/gaussian-weighted mean of its
    block_size neighbourhood minus c. Good for uneven lighting (documents, text)."""
    src = to_uint8_gray(image)
    out = cv2.adaptiveThreshold(
        src,
        max_value,
        cv2.ADAPTIVE_THRESH_MEAN_C if method == "mean" else cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY,
        max(3, odd(block_size)),
        c,
    )
    return Result([out])
