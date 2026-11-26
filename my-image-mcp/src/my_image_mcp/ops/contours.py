"""Contour extraction, measurement and manipulation.

Contour sets are stored as their own handle kind ("cnt_N") so they can be
filtered, approximated and drawn without re-detecting.
"""

from __future__ import annotations

import math
from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Contours, Img, Result
from ._util import to_uint8_gray

_MODES = {"external": cv2.RETR_EXTERNAL, "list": cv2.RETR_LIST, "tree": cv2.RETR_TREE, "ccomp": cv2.RETR_CCOMP}


def _props(c: np.ndarray, index: int) -> dict:
    area = float(cv2.contourArea(c))
    perim = float(cv2.arcLength(c, True))
    x, y, w, h = cv2.boundingRect(c)
    m = cv2.moments(c)
    cx, cy = (m["m10"] / m["m00"], m["m01"] / m["m00"]) if m["m00"] else (x + w / 2, y + h / 2)
    return {
        "index": index,
        "area": round(area, 2),
        "perimeter": round(perim, 2),
        "bbox": [int(x), int(y), int(w), int(h)],
        "centroid": [round(cx, 2), round(cy, 2)],
        "circularity": round(4 * math.pi * area / (perim * perim), 4) if perim else 0.0,
        "points": int(len(c)),
    }


def _summary(cs: list[np.ndarray], max_report: int) -> dict:
    items = [_props(c, i) for i, c in enumerate(cs[:max_report])]
    return {"count": len(cs), "contours": items, "truncated": len(cs) > max_report}


@op("contours")
def find_contours(
    image: Img,
    mode: Literal["external", "list", "tree", "ccomp"] = "external",
    approximation: Literal["simple", "none"] = "simple",
    min_area: float = 0.0,
    max_area: float | None = None,
    sort_by_area: bool = True,
    max_report: int = 50,
) -> Result:
    """Find contours in a binary image (non-zero = foreground; threshold or canny first).
    Returns a contour-set handle plus per-contour area, perimeter, bbox, centroid, circularity.
    Filtered by area and (by default) sorted largest first; indices refer to the returned set."""
    binary = to_uint8_gray(image)
    method = cv2.CHAIN_APPROX_SIMPLE if approximation == "simple" else cv2.CHAIN_APPROX_NONE
    found, _ = cv2.findContours(binary, _MODES[mode], method)
    cs = [c for c in found if cv2.contourArea(c) >= min_area and (max_area is None or cv2.contourArea(c) <= max_area)]
    if sort_by_area:
        cs.sort(key=lambda c: -cv2.contourArea(c))
    return Result([Contours(cs)], {"total_found": len(found), **_summary(cs, max_report)})


@op("contours")
def contour_properties(contours: Contours, max_report: int = 100) -> Result:
    """Detailed geometry per contour: rotated rect, enclosing circle, fitted ellipse, convex hull area,
    solidity, extent, aspect ratio, Hu moments. No image output."""
    items = []
    for i, c in enumerate(contours[:max_report]):
        p = _props(c, i)
        x, y, w, h = p["bbox"]
        (rx, ry), (rw, rh), ang = cv2.minAreaRect(c)
        (ex, ey), er = cv2.minEnclosingCircle(c)
        hull_area = float(cv2.contourArea(cv2.convexHull(c)))
        p.update(
            {
                "rotated_rect": {
                    "center": [round(rx, 2), round(ry, 2)],
                    "size": [round(rw, 2), round(rh, 2)],
                    "angle": round(ang, 2),
                },
                "enclosing_circle": {"center": [round(ex, 2), round(ey, 2)], "radius": round(er, 2)},
                "aspect_ratio": round(w / h, 4) if h else None,
                "extent": round(p["area"] / (w * h), 4) if w * h else None,
                "solidity": round(p["area"] / hull_area, 4) if hull_area else None,
                "convex": bool(cv2.isContourConvex(c)),
                "hu_moments": [float(v) for v in cv2.HuMoments(cv2.moments(c)).flatten()],
            }
        )
        if len(c) >= 5:
            (fx, fy), (fa, fb), fang = cv2.fitEllipse(c)
            p["ellipse"] = {
                "center": [round(fx, 2), round(fy, 2)],
                "axes": [round(fa, 2), round(fb, 2)],
                "angle": round(fang, 2),
            }
        items.append(p)
    return Result([], {"count": len(contours), "contours": items, "truncated": len(contours) > max_report})


@op("contours")
def filter_contours(
    contours: Contours,
    min_area: float = 0.0,
    max_area: float | None = None,
    min_circularity: float = 0.0,
    max_circularity: float = 1.01,
    min_aspect_ratio: float = 0.0,
    max_aspect_ratio: float | None = None,
    indices: list[int] | None = None,
    max_report: int = 50,
) -> Result:
    """Keep contours matching all criteria (aspect ratio = bbox width / height), or pick by indices."""
    out = []
    for i, c in enumerate(contours):
        if indices is not None:
            if i in indices:
                out.append(c)
            continue
        p = _props(c, i)
        _, _, w, h = p["bbox"]
        ar = w / h if h else 0
        if p["area"] < min_area or (max_area is not None and p["area"] > max_area):
            continue
        if not (min_circularity <= p["circularity"] <= max_circularity):
            continue
        if ar < min_aspect_ratio or (max_aspect_ratio is not None and ar > max_aspect_ratio):
            continue
        out.append(c)
    return Result([Contours(out)], _summary(out, max_report))


@op("contours")
def approximate_contours(contours: Contours, epsilon_ratio: float = 0.02, closed: bool = True) -> Result:
    """Simplify contours to polygons (Douglas-Peucker, epsilon = ratio * perimeter) and guess a
    shape name from the vertex count and circularity (triangle, quadrilateral, ..., circle)."""
    out, shapes = [], []
    for i, c in enumerate(contours):
        a = cv2.approxPolyDP(c, epsilon_ratio * cv2.arcLength(c, closed), closed)
        out.append(a)
        n = len(a)
        circ = _props(c, i)["circularity"]
        if n >= 7 and circ > 0.8:
            name = "circle" if circ > 0.85 else "ellipse/round"
        else:
            name = {3: "triangle", 4: "quadrilateral", 5: "pentagon", 6: "hexagon"}.get(n, f"{n}-gon")
        pts = a.reshape(-1, 2).tolist() if n <= 12 else None
        shapes.append({"index": i, "vertices": n, "shape": name, "points": pts})
    return Result([Contours(out)], {"count": len(out), "shapes": shapes[:100]})


@op("contours")
def convex_hulls(contours: Contours) -> Result:
    """Replace each contour by its convex hull."""
    return Result([Contours([cv2.convexHull(c) for c in contours])], {"count": len(contours)})


@op("contours")
def contour_mask(contours: Contours, like: Img, thickness: int = -1, index: int = -1) -> Result:
    """Rasterize contours into a single-channel mask the size of 'like' (filled when thickness=-1).
    index=-1 draws all contours."""
    mask = np.zeros(like.shape[:2], np.uint8)
    cv2.drawContours(mask, list(contours), index, 255, thickness)
    return Result([mask], {"area_pixels": int(np.count_nonzero(mask))})
