"""Morphological operations and binary-image analysis."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import to_uint8_gray

_OPS = {
    "erode": cv2.MORPH_ERODE,
    "dilate": cv2.MORPH_DILATE,
    "open": cv2.MORPH_OPEN,
    "close": cv2.MORPH_CLOSE,
    "gradient": cv2.MORPH_GRADIENT,
    "tophat": cv2.MORPH_TOPHAT,
    "blackhat": cv2.MORPH_BLACKHAT,
}
_SHAPES = {"rect": cv2.MORPH_RECT, "ellipse": cv2.MORPH_ELLIPSE, "cross": cv2.MORPH_CROSS}


def _binary(image: np.ndarray) -> np.ndarray:
    g = to_uint8_gray(image)
    return np.where(g > 0, 255, 0).astype(np.uint8)


@op("morphology")
def morphology(
    image: Img,
    operation: Literal["erode", "dilate", "open", "close", "gradient", "tophat", "blackhat"] = "open",
    kernel_shape: Literal["rect", "ellipse", "cross"] = "ellipse",
    ksize: int = 3,
    iterations: int = 1,
) -> Result:
    """Morphological operation. open removes small specks, close fills small holes/gaps,
    gradient gives outlines, tophat/blackhat extract small bright/dark details."""
    kernel = cv2.getStructuringElement(_SHAPES[kernel_shape], (max(1, ksize), max(1, ksize)))
    return Result([cv2.morphologyEx(image, _OPS[operation], kernel, iterations=max(1, iterations))])


@op("morphology")
def distance_transform(
    image: Img,
    metric: Literal["l1", "l2", "c"] = "l2",
    mask_size: Literal[0, 3, 5] = 5,
    normalize_output: bool = True,
) -> Result:
    """Distance of every foreground (non-zero) pixel to the nearest background pixel.
    normalize_output=True returns a viewable 8-bit map; otherwise float32 distances in pixels."""
    dist_type = {"l1": cv2.DIST_L1, "l2": cv2.DIST_L2, "c": cv2.DIST_C}[metric]
    d = cv2.distanceTransform(_binary(image), dist_type, mask_size)
    max_d = float(d.max())
    out = cv2.normalize(d, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U) if normalize_output else d
    return Result([out], {"max_distance": round(max_d, 3)})


@op("morphology")
def connected_components(
    image: Img, connectivity: Literal[4, 8] = 8, min_area: int = 0, max_report: int = 100
) -> Result:
    """Label connected foreground regions of a binary image. Outputs [labels (int32), colorized view]
    and per-component stats (area, bbox, centroid) sorted by area, largest first."""
    n, labels, stats, cents = cv2.connectedComponentsWithStats(_binary(image), connectivity=connectivity)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area
    keep[0] = True
    labels = np.where(keep[labels], labels, 0)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not keep[i]:
            continue
        comps.append(
            {
                "label": i,
                "area": area,
                "bbox": [x, y, w, h],
                "centroid": [round(float(cents[i][0]), 2), round(float(cents[i][1]), 2)],
            }
        )
    comps.sort(key=lambda c: -c["area"])
    rng = np.random.default_rng(42)
    palette = rng.integers(40, 256, size=(n, 3), dtype=np.uint8)
    palette[0] = 0
    color = palette[labels]
    return Result(
        [labels.astype(np.int32), color],
        {"count": len(comps), "components": comps[:max_report], "truncated": len(comps) > max_report},
    )


@op("morphology")
def skeletonize(image: Img) -> Result:
    """Thin binary shapes down to 1-pixel-wide skeletons (Zhang-Suen via ximgproc when available)."""
    b = _binary(image)
    if hasattr(cv2, "ximgproc"):
        return Result([cv2.ximgproc.thinning(b)])
    skel = np.zeros_like(b)
    elem = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    while cv2.countNonZero(b):
        eroded = cv2.erode(b, elem)
        skel |= cv2.subtract(b, cv2.dilate(eroded, elem))
        b = eroded
    return Result([skel])


@op("morphology")
def fill_holes(image: Img) -> Result:
    """Fill holes (background regions fully enclosed by foreground) in a binary image."""
    b = _binary(image)
    h, w = b.shape
    padded = cv2.copyMakeBorder(b, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    cv2.floodFill(flood, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 255)
    holes = cv2.bitwise_not(flood)[1:-1, 1:-1]
    return Result([cv2.bitwise_or(b, holes)], {"filled_pixels": int(np.count_nonzero(holes))})
