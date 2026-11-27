"""Resizing, cropping and geometric transforms."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import border, channels, interp, parse_color, to_bgr, to_uint8

Interp = Literal["auto", "nearest", "linear", "cubic", "area", "lanczos"]


@op("geometry")
def resize(
    image: Img,
    width: int | None = None,
    height: int | None = None,
    scale: float | None = None,
    interpolation: Interp = "auto",
) -> Result:
    """Resize by scale, or to width and/or height (aspect ratio kept if only one is given).
    'auto' interpolation uses area for shrinking and cubic for enlarging."""
    h, w = image.shape[:2]
    if scale is not None:
        nw, nh = round(w * scale), round(h * scale)
    elif width and height:
        nw, nh = width, height
    elif width:
        nw, nh = width, round(h * width / w)
    elif height:
        nw, nh = round(w * height / h), height
    else:
        raise ValueError("Give scale, width or height.")
    nw, nh = max(1, int(nw)), max(1, int(nh))
    if interpolation == "auto":
        flag = cv2.INTER_AREA if nw * nh < w * h else cv2.INTER_CUBIC
    else:
        flag = interp(interpolation)
    return Result([cv2.resize(image, (nw, nh), interpolation=flag)])


@op("geometry")
def crop(image: Img, x: int, y: int, width: int, height: int) -> Result:
    """Crop a rectangle (clamped to the image bounds)."""
    h, w = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + width), min(h, y + height)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"Crop rectangle is outside the {w}x{h} image.")
    return Result([image[y0:y1, x0:x1].copy()], {"rect": [x0, y0, x1 - x0, y1 - y0]})


@op("geometry")
def rotate(
    image: Img,
    angle: float,
    expand: bool = True,
    center: list[float] | None = None,
    scale: float = 1.0,
    border_color: str = "#000000",
    interpolation: Interp = "linear",
) -> Result:
    """Rotate counter-clockwise by angle degrees. expand=True grows the canvas so nothing is cut off.
    Multiples of 90 are lossless."""
    if angle % 90 == 0 and scale == 1.0 and center is None:
        k = int(angle // 90) % 4
        codes = {1: cv2.ROTATE_90_COUNTERCLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_CLOCKWISE}
        return Result([image.copy() if k == 0 else cv2.rotate(image, codes[k])])
    h, w = image.shape[:2]
    cx, cy = center if center else (w / 2, h / 2)
    m = cv2.getRotationMatrix2D((cx, cy), angle, scale)
    nw, nh = w, h
    if expand:
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(h * sin + w * cos + 0.5), int(h * cos + w * sin + 0.5)
        m[0, 2] += nw / 2 - cx
        m[1, 2] += nh / 2 - cy
    flag = cv2.INTER_LINEAR if interpolation == "auto" else interp(interpolation)
    out = cv2.warpAffine(
        image,
        m,
        (nw, nh),
        flags=flag,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=parse_color(border_color, channels(image)),
    )
    return Result([out], {"matrix": m.tolist()})


@op("geometry")
def flip(image: Img, direction: Literal["horizontal", "vertical", "both"] = "horizontal") -> Result:
    """Mirror the image horizontally (left-right), vertically (up-down) or both."""
    return Result([cv2.flip(image, {"horizontal": 1, "vertical": 0, "both": -1}[direction])])


@op("geometry")
def pad(
    image: Img,
    top: int = 0,
    bottom: int = 0,
    left: int = 0,
    right: int = 0,
    mode: Literal["constant", "reflect", "replicate", "wrap"] = "constant",
    color: str = "#000000",
) -> Result:
    """Add a border around the image."""
    out = cv2.copyMakeBorder(image, top, bottom, left, right, border(mode), value=parse_color(color, channels(image)))
    return Result([out])


@op("geometry")
def warp_affine(
    image: Img,
    matrix: list[list[float]],
    width: int | None = None,
    height: int | None = None,
    interpolation: Interp = "linear",
    border_color: str = "#000000",
) -> Result:
    """Apply a 2x3 affine matrix. Output size defaults to the input size."""
    m = np.array(matrix, dtype=np.float64)
    if m.shape != (2, 3):
        raise ValueError("matrix must be 2x3")
    h, w = image.shape[:2]
    flag = cv2.INTER_LINEAR if interpolation == "auto" else interp(interpolation)
    out = cv2.warpAffine(
        image, m, (width or w, height or h), flags=flag, borderValue=parse_color(border_color, channels(image))
    )
    return Result([out])


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


@op("geometry")
def warp_perspective(
    image: Img,
    src_points: list[list[float]],
    dst_points: list[list[float]] | None = None,
    width: int | None = None,
    height: int | None = None,
    interpolation: Interp = "linear",
) -> Result:
    """Perspective warp from 4 source points. Without dst_points the quad is rectified to a
    straight rectangle (e.g. scanning a photographed document; corners may be in any order)."""
    src = np.array(src_points, dtype=np.float32)
    if src.shape != (4, 2):
        raise ValueError("src_points must be 4 [x, y] points")
    if dst_points is None:
        src = _order_quad(src)
        tl, tr, br, bl = src
        w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
        h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
        width, height = width or w, height or h
        dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    else:
        dst = np.array(dst_points, dtype=np.float32)
        if dst.shape != (4, 2):
            raise ValueError("dst_points must be 4 [x, y] points")
        ih, iw = image.shape[:2]
        width, height = width or iw, height or ih
    m = cv2.getPerspectiveTransform(src, dst)
    flag = cv2.INTER_LINEAR if interpolation == "auto" else interp(interpolation)
    out = cv2.warpPerspective(image, m, (int(width), int(height)), flags=flag)
    return Result([out], {"matrix": m.tolist()})


_STITCH_ERRORS = {
    cv2.Stitcher_ERR_NEED_MORE_IMGS: "not enough overlap/features to connect the images",
    cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "could not estimate the transform between images (too little overlap?)",
    cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "camera parameter adjustment failed",
}


@op("geometry")
def stitch_images(images: list[Img], mode: Literal["panorama", "scans"] = "panorama", trim: bool = True) -> Result:
    """Stitch overlapping images into one. panorama: photos taken by rotating the camera;
    scans: flat documents/maps shifted under the camera (affine). Images need roughly 20-50% overlap,
    order doesn't matter. trim=True cuts the empty margins around the result."""
    if len(images) < 2:
        raise ValueError("need at least 2 images")
    st = cv2.Stitcher_create(cv2.Stitcher_PANORAMA if mode == "panorama" else cv2.Stitcher_SCANS)
    status, pano = st.stitch([to_bgr(to_uint8(i)) for i in images])
    if status != cv2.Stitcher_OK:
        raise ValueError(f"stitching failed: {_STITCH_ERRORS.get(status, f'status {status}')}")
    if trim:
        ys, xs = np.nonzero(cv2.cvtColor(pano, cv2.COLOR_BGR2GRAY))
        if len(xs):
            pano = pano[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].copy()
    return Result([pano], {"width": int(pano.shape[1]), "height": int(pano.shape[0])})


@op("geometry")
def trim(image: Img, fuzz: float = 5.0, background: str | None = None) -> Result:
    """Cut away uniform margins (scanned pages, screenshots, renders on a plain background).
    The background is the top-left pixel's color unless given; fuzz (% of full range) is the
    tolerance. Returns the crop box [x, y, w, h] in input coordinates."""
    ch = channels(image)
    maxval = 65535.0 if image.dtype == np.uint16 else (1.0 if image.dtype.kind == "f" else 255.0)
    if background is None:
        bg = image[0, 0].astype(np.float32)
    else:
        bg = np.array(parse_color(background, ch), np.float32) * (maxval / 255.0)
    diff = np.abs(image.astype(np.float32) - bg)
    if diff.ndim == 3:
        diff = diff.max(axis=2)
    ys, xs = np.nonzero(diff > fuzz / 100.0 * maxval)
    if not len(xs):
        return Result([image.copy()], {"box": [0, 0, image.shape[1], image.shape[0]], "note": "image is uniform"})
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return Result([image[y0:y1, x0:x1].copy()], {"box": [x0, y0, x1 - x0, y1 - y0]})
