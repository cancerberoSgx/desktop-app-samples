"""Combining images: blending, masks, bitwise logic, differences, overlays."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import channels, match_to, parse_color, to_bgr, to_gray


@op("composite")
def blend(image1: Img, image2: Img, alpha: float = 0.5, beta: float | None = None, gamma: float = 0.0) -> Result:
    """Weighted sum alpha*image1 + beta*image2 + gamma (beta defaults to 1-alpha).
    image2 is resized/converted to match image1."""
    b = match_to(image2, image1)
    return Result([cv2.addWeighted(image1, alpha, b, 1 - alpha if beta is None else beta, gamma)])


@op("composite")
def bitwise(
    image1: Img, operation: Literal["and", "or", "xor", "not"], image2: Img | None = None, mask: Img | None = None
) -> Result:
    """Bitwise and/or/xor of two images (or 'not' of one), optionally restricted to a mask.
    Typical use: combining binary masks."""
    m = None
    if mask is not None:
        m = to_gray(mask)
        if m.shape[:2] != image1.shape[:2]:
            m = cv2.resize(m, (image1.shape[1], image1.shape[0]), interpolation=cv2.INTER_NEAREST)
        m = np.where(m > 0, 255, 0).astype(np.uint8)
    if operation == "not":
        return Result([cv2.bitwise_not(image1, mask=m)])
    if image2 is None:
        raise ValueError(f"'{operation}' needs image2")
    fn = {"and": cv2.bitwise_and, "or": cv2.bitwise_or, "xor": cv2.bitwise_xor}[operation]
    return Result([fn(image1, match_to(image2, image1), mask=m)])


@op("composite")
def apply_mask(image: Img, mask: Img, background: str = "#000000", transparent: bool = False) -> Result:
    """Keep pixels where mask is non-zero. Elsewhere use the background color, or make them
    transparent (returns BGRA) with transparent=True."""
    m = to_gray(mask)
    if m.shape[:2] != image.shape[:2]:
        m = cv2.resize(m, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
    keep = m > 0
    if transparent:
        out = cv2.cvtColor(to_bgr(image), cv2.COLOR_BGR2BGRA)
        out[:, :, 3] = np.where(keep, 255, 0).astype(out.dtype)
        return Result([out])
    out = np.empty_like(image)
    out[:] = parse_color(background, channels(image)) if channels(image) > 1 else parse_color(background, 1)[0]
    out[keep] = image[keep]
    return Result([out], {"kept_ratio": round(float(keep.mean()), 4)})


@op("composite")
def difference(image1: Img, image2: Img, threshold: int = 25) -> Result:
    """Absolute difference |image1 - image2| plus a binary change mask (diff > threshold).
    Outputs [difference, change_mask] and change statistics."""
    b = match_to(image2, image1)
    diff = cv2.absdiff(image1, b)
    g = to_gray(diff)
    mask = np.where(g > threshold, 255, 0).astype(np.uint8)
    return Result(
        [diff, mask],
        {
            "mean_difference": round(float(g.mean()), 4),
            "changed_ratio": round(float(np.count_nonzero(mask)) / mask.size, 4),
        },
    )


@op("composite")
def overlay(base: Img, top: Img, x: int = 0, y: int = 0, opacity: float = 1.0) -> Result:
    """Paste 'top' onto 'base' with its top-left corner at (x, y). Uses top's alpha channel when it
    has one, times opacity. Parts outside the base are clipped."""
    out = base.copy()
    bh, bw = base.shape[:2]
    th, tw = top.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(bw, x + tw), min(bh, y + th)
    if x1 <= x0 or y1 <= y0:
        return Result([out], {"note": "overlay is completely outside the base image"})
    tile = top[y0 - y : y1 - y, x0 - x : x1 - x]
    if channels(tile) == 4:
        alpha = tile[:, :, 3:4].astype(np.float32) / (65535.0 if tile.dtype == np.uint16 else 255.0)
        tile = tile[:, :, :3]
    else:
        alpha = np.ones(tile.shape[:2] + (1,), np.float32)
    alpha *= opacity
    region = out[y0:y1, x0:x1]
    color_tile = match_to(tile, region, resize=False)
    if channels(region) == 1:
        alpha = alpha[:, :, 0]
    blended = region.astype(np.float32) * (1 - alpha) + color_tile.astype(np.float32) * alpha
    if channels(region) == 4:
        blended[:, :, 3] = region[:, :, 3]
    out[y0:y1, x0:x1] = blended.astype(base.dtype)
    return Result([out])
