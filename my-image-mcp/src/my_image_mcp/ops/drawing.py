"""Annotation: shapes, text, contours, boxes and markers.

Every op returns a new image; the input is never modified. Gray images are
promoted to BGR automatically when a non-gray color is used.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import REGISTRY, op
from ..core.types import Contours, Img, Result
from ._util import channels, ensure_color_for, parse_color

_FONTS = {
    "simplex": cv2.FONT_HERSHEY_SIMPLEX,
    "plain": cv2.FONT_HERSHEY_PLAIN,
    "duplex": cv2.FONT_HERSHEY_DUPLEX,
    "complex": cv2.FONT_HERSHEY_COMPLEX,
    "triplex": cv2.FONT_HERSHEY_TRIPLEX,
    "script": cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
}
Font = Literal["simplex", "plain", "duplex", "complex", "triplex", "script"]


def _prep(image: np.ndarray, color: str) -> tuple[np.ndarray, tuple]:
    out = ensure_color_for(image, color)
    return out, parse_color(color, channels(out))


def _lt(antialias: bool) -> int:
    return cv2.LINE_AA if antialias else cv2.LINE_8


def _text(
    img: np.ndarray,
    text: str,
    org: tuple[int, int],
    scale: float,
    color: tuple,
    thickness: int,
    font: int,
    bg: tuple | None,
    aa: bool,
) -> None:
    if bg is not None:
        (tw, th), base = cv2.getTextSize(text, font, scale, thickness)
        x, y = org
        cv2.rectangle(img, (x - 2, y - th - 4), (x + tw + 2, y + base), bg, -1)
    cv2.putText(img, text, org, font, scale, color, thickness, _lt(aa))


@op("drawing")
def draw_line(
    image: Img,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: str = "red",
    thickness: int = 2,
    arrow: bool = False,
    antialias: bool = True,
) -> Result:
    """Draw a line from (x1,y1) to (x2,y2), optionally with an arrow head at the end."""
    out, c = _prep(image, color)
    if arrow:
        cv2.arrowedLine(out, (x1, y1), (x2, y2), c, thickness, _lt(antialias), tipLength=0.1)
    else:
        cv2.line(out, (x1, y1), (x2, y2), c, thickness, _lt(antialias))
    return Result([out])


@op("drawing")
def draw_rectangle(
    image: Img, x: int, y: int, width: int, height: int, color: str = "red", thickness: int = 2, antialias: bool = True
) -> Result:
    """Draw a rectangle; thickness=-1 fills it."""
    out, c = _prep(image, color)
    cv2.rectangle(out, (x, y), (x + width, y + height), c, thickness, _lt(antialias))
    return Result([out])


@op("drawing")
def draw_circle(
    image: Img, cx: int, cy: int, radius: int, color: str = "red", thickness: int = 2, antialias: bool = True
) -> Result:
    """Draw a circle; thickness=-1 fills it."""
    out, c = _prep(image, color)
    cv2.circle(out, (cx, cy), radius, c, thickness, _lt(antialias))
    return Result([out])


@op("drawing")
def draw_ellipse(
    image: Img,
    cx: int,
    cy: int,
    axis_x: int,
    axis_y: int,
    angle: float = 0.0,
    start_angle: float = 0.0,
    end_angle: float = 360.0,
    color: str = "red",
    thickness: int = 2,
    antialias: bool = True,
) -> Result:
    """Draw an ellipse (or arc) with half-axes axis_x/axis_y rotated by angle degrees."""
    out, c = _prep(image, color)
    cv2.ellipse(out, (cx, cy), (axis_x, axis_y), angle, start_angle, end_angle, c, thickness, _lt(antialias))
    return Result([out])


@op("drawing")
def draw_polygon(
    image: Img,
    points: list[list[int]],
    closed: bool = True,
    fill: bool = False,
    color: str = "red",
    thickness: int = 2,
    antialias: bool = True,
) -> Result:
    """Draw a polyline/polygon through [[x,y], ...]; fill=True fills it."""
    out, c = _prep(image, color)
    pts = np.array(points, dtype=np.int32).reshape(-1, 1, 2)
    if fill:
        cv2.fillPoly(out, [pts], c, _lt(antialias))
    else:
        cv2.polylines(out, [pts], closed, c, thickness, _lt(antialias))
    return Result([out])


@op("drawing")
def draw_text(
    image: Img,
    text: str,
    x: int,
    y: int,
    font_scale: float = 1.0,
    color: str = "white",
    thickness: int = 2,
    font: Font = "simplex",
    background: str | None = None,
    antialias: bool = True,
) -> Result:
    """Draw text with its baseline-left at (x, y). Optional background box color. ASCII only
    (OpenCV's Hershey fonts)."""
    out, c = _prep(image, color)
    bg = parse_color(background, channels(out)) if background else None
    for i, line in enumerate(text.split("\n")):
        dy = int(i * font_scale * 35)
        _text(out, line, (x, y + dy), font_scale, c, thickness, _FONTS[font], bg, antialias)
    return Result([out])


@op("drawing")
def draw_contours(
    image: Img,
    contours: Contours,
    index: int = -1,
    color: str = "lime",
    thickness: int = 2,
    label: bool = False,
    antialias: bool = True,
) -> Result:
    """Draw a contour set (index=-1 for all, thickness=-1 fills). label=True writes each index at its centroid."""
    out, c = _prep(image, color)
    cv2.drawContours(out, list(contours), index, c, thickness, _lt(antialias))
    if label:
        for i, cnt in enumerate(contours):
            if index not in (-1, i):
                continue
            m = cv2.moments(cnt)
            if m["m00"]:
                cv2.putText(
                    out,
                    str(i),
                    (int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    c,
                    1,
                    cv2.LINE_AA,
                )
    return Result([out])


@op("drawing")
def draw_boxes(
    image: Img,
    boxes: list[list[float]],
    labels: list[str] | None = None,
    color: str = "lime",
    thickness: int = 2,
    font_scale: float = 0.6,
    normalized: bool = False,
) -> Result:
    """Draw many labelled bounding boxes at once, each [x, y, width, height]. normalized=True means
    coordinates are fractions (0-1) of the image size. Handy for annotating detections."""
    out, c = _prep(image, color)
    h, w = out.shape[:2]
    text_c = parse_color("black", channels(out))
    for i, b in enumerate(boxes):
        x, y, bw, bh = b
        if normalized:
            x, y, bw, bh = x * w, y * h, bw * w, bh * h
        p1, p2 = (int(x), int(y)), (int(x + bw), int(y + bh))
        cv2.rectangle(out, p1, p2, c, thickness, cv2.LINE_AA)
        if labels and i < len(labels) and labels[i]:
            _text(
                out,
                labels[i],
                (p1[0] + 2, max(p1[1] - 4, 12)),
                font_scale,
                text_c,
                1,
                cv2.FONT_HERSHEY_SIMPLEX,
                c,
                True,
            )
    return Result([out], {"count": len(boxes)})


@op("drawing")
def draw_markers(
    image: Img,
    points: list[list[int]],
    marker: Literal[
        "cross", "tilted_cross", "star", "diamond", "square", "triangle_up", "triangle_down", "dot"
    ] = "cross",
    size: int = 12,
    color: str = "red",
    thickness: int = 2,
) -> Result:
    """Draw a marker at each [x, y] point (e.g. keypoints, centroids, corners)."""
    out, c = _prep(image, color)
    for x, y in points:
        if marker == "dot":
            cv2.circle(out, (int(x), int(y)), max(1, size // 4), c, -1, cv2.LINE_AA)
        else:
            mt = getattr(cv2, f"MARKER_{marker.upper()}")
            cv2.drawMarker(out, (int(x), int(y)), c, mt, size, thickness, cv2.LINE_AA)
    return Result([out], {"count": len(points)})


# Appended to every drawing tool's description, because models tend to fire
# several drawing calls on the same image in parallel and lose all but one.
_CHAIN_NOTE = (
    "Returns a NEW image handle; the input is unchanged. To draw several things, pass each "
    "call's output to the next call (sequentially, never in parallel on the same handle), "
    'or do them all in one run_pipeline call using "$prev".'
)
for _spec in REGISTRY.values():
    if _spec.module == "drawing":
        _spec.description = f"{_spec.description}\n\n{_CHAIN_NOTE}"
