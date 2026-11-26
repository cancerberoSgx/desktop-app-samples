"""Segmentation: color quantization, flood fill, watershed, GrabCut,
superpixels and mean-shift.

Label images are int32 (0 = background / unlabelled); preview and save
normalise them for display. Randomised algorithms are seeded so pipelines
replay identically.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Contours, Img, Result
from ._util import channels, parse_color, to_bgr, to_uint8, to_uint8_gray


def _hex(bgr) -> str:
    b, g, r = (int(round(float(v))) for v in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mask(image: np.ndarray) -> np.ndarray:
    g = to_uint8_gray(image)
    return np.where(g > 0, 255, 0).astype(np.uint8)


def _regions(labels: np.ndarray, max_report: int) -> tuple[Contours, list[dict]]:
    cs, items = [], []
    for lab in range(1, int(labels.max()) + 1):
        m = (labels == lab).astype(np.uint8)
        area = int(m.sum())
        if not area:
            continue
        found, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cs.append(max(found, key=cv2.contourArea))
        if len(items) < max_report:
            x, y, w, h = cv2.boundingRect(m)
            mo = cv2.moments(m, True)
            items.append(
                {
                    "label": lab,
                    "area": area,
                    "bbox": [int(x), int(y), int(w), int(h)],
                    "centroid": [round(mo["m10"] / mo["m00"], 2), round(mo["m01"] / mo["m00"], 2)],
                }
            )
    return Contours(cs), items


@op("segmentation")
def kmeans_colors(image: Img, k: int = 4, attempts: int = 3, sample: int = 100_000) -> Result:
    """Reduce an image to its k dominant colors (k-means). Returns the posterized image and a label
    image (0..k-1), plus the palette as RGB hex with pixel ratios, most common first. Good for
    "what are the main colors" and as a quick color segmentation (follow with in_range or compare labels)."""
    px = to_uint8(image).reshape(-1, channels(image)).astype(np.float32)
    rng = np.random.default_rng(0)
    fit = px[rng.choice(len(px), sample, replace=False)] if len(px) > sample else px
    cv2.setRNGSeed(0)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, _, centers = cv2.kmeans(fit, k, None, crit, attempts, cv2.KMEANS_PP_CENTERS)
    labels = np.empty(len(px), np.int32)
    for i in range(0, len(px), 500_000):  # chunked nearest-center assignment
        chunk = px[i : i + 500_000]
        labels[i : i + len(chunk)] = np.argmin(((chunk[:, None, :] - centers[None]) ** 2).sum(-1), axis=1)
    counts = np.bincount(labels, minlength=k)
    order = np.argsort(-counts)  # relabel so 0 = most common color
    remap = np.empty(k, np.int32)
    remap[order] = np.arange(k)
    labels = remap[labels]
    centers = centers[order]
    quant = centers.round().clip(0, 255).astype(np.uint8)[labels].reshape(image.shape[:2] + (-1,))
    if image.ndim == 2:
        quant = quant[:, :, 0]
    total = float(len(px))
    palette = []
    for c, n in zip(centers, counts[order]):
        bgr = list(c) * 3 if len(c) == 1 else c[:3]
        palette.append({"color": _hex(bgr), "ratio": round(float(n) / total, 4)})
    return Result([quant, labels.reshape(image.shape[:2])], {"palette": palette})


@op("segmentation")
def flood_fill(
    image: Img,
    x: int,
    y: int,
    color: str = "red",
    tolerance: int = 10,
    connectivity: Literal[4, 8] = 4,
    relative_to_seed: bool = True,
) -> Result:
    """Fill the connected region around seed (x, y) whose pixels are within tolerance of the seed
    (relative_to_seed=True) or of their neighbour (False, follows gradients). Returns the filled image
    and the region mask, plus area and bbox. Handy to select a uniform background or object by clicking."""
    h, w = image.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        raise ValueError(f"seed ({x}, {y}) is outside the {w}x{h} image")
    out = to_uint8(image).copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = connectivity | (255 << 8) | (cv2.FLOODFILL_FIXED_RANGE if relative_to_seed else 0)
    tol = (tolerance,) * 4
    area, _, _, rect = cv2.floodFill(out, mask, (x, y), parse_color(color, channels(out)), tol, tol, flags)
    return Result([out, mask[1:-1, 1:-1].copy()], {"area": int(area), "bbox": [int(v) for v in rect]})


@op("segmentation")
def watershed(
    image: Img,
    mask: Img | None = None,
    separation: float = 0.6,
    max_report: int = 50,
) -> Result:
    """Split touching/overlapping objects (coins, cells, grains) in a foreground mask. mask: binary
    foreground (non-zero); if omitted, Otsu threshold of image is used. separation (0-1): how far from
    an object's edge (relative to the thickest part) a seed must be; raise it if touching objects
    stay merged, lower it if a single object gets split. Returns a label image (0 = background),
    one contour per object and per-object area/bbox/centroid."""
    if mask is None:
        _, fg = cv2.threshold(to_uint8_gray(image), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        fg = _mask(mask)
        if fg.shape != image.shape[:2]:
            raise ValueError("mask must have the same size as image")
    dist = cv2.distanceTransform(fg, cv2.DIST_L2, 5)
    sure_fg = np.uint8(dist > separation * dist.max()) * 255 if dist.max() > 0 else np.zeros_like(fg)
    sure_bg = cv2.dilate(fg, np.ones((3, 3), np.uint8), iterations=3)
    _, markers = cv2.connectedComponents(sure_fg)
    markers = markers + 1  # background = 1, objects = 2..n
    markers[(sure_bg > 0) & (sure_fg == 0)] = 0  # unknown band, decided by watershed
    cv2.watershed(to_bgr(to_uint8(image)).copy(), markers)
    labels = np.where(markers > 1, markers - 1, 0).astype(np.int32)
    labels[fg == 0] = 0
    contours, items = _regions(labels, max_report)
    return Result(
        [labels, contours], {"count": len(contours), "objects": items, "truncated": len(contours) > max_report}
    )


@op("segmentation")
def grabcut(
    image: Img,
    x: int,
    y: int,
    width: int,
    height: int,
    iterations: int = 5,
    hint: Img | None = None,
) -> Result:
    """Extract the foreground object inside a rectangle (GrabCut). Everything outside the rectangle is
    background. Optional hint: same-size gray image where 255 = surely foreground, 0 = surely
    background, other values = no hint (draw strokes on create_image(..., color='gray') to fix
    mistakes). Returns a binary mask (255 = object); use apply_mask for a cutout."""
    img = to_bgr(to_uint8(image))
    h, w = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + width), min(h, y + height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("rectangle is empty or outside the image")
    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[y0:y1, x0:x1] = cv2.GC_PR_FGD
    if hint is not None:
        hg = to_uint8_gray(hint)
        if hg.shape != (h, w):
            raise ValueError("hint must have the same size as image")
        mask[hg == 255] = cv2.GC_FGD
        mask[hg == 0] = cv2.GC_BGD
    cv2.setRNGSeed(0)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(img, mask, None, bgd, fgd, iterations, cv2.GC_INIT_WITH_MASK)
    out = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    return Result([out], {"foreground_ratio": round(float((out > 0).mean()), 4)})


@op("segmentation")
def superpixels(
    image: Img,
    region_size: int = 30,
    ruler: float = 10.0,
    iterations: int = 10,
    algorithm: Literal["slic", "slico", "mslic"] = "slico",
    color: str = "yellow",
) -> Result:
    """Over-segment into compact superpixels (SLIC). region_size = average superpixel size in px;
    ruler = compactness (higher = more regular, slic/mslic only). Returns the image with boundaries
    drawn, a label image (0..count-1) and an image where each superpixel is filled with its mean color."""
    img = to_bgr(to_uint8(image))
    algo = {"slic": cv2.ximgproc.SLIC, "slico": cv2.ximgproc.SLICO, "mslic": cv2.ximgproc.MSLIC}[algorithm]
    sp = cv2.ximgproc.createSuperpixelSLIC(cv2.cvtColor(img, cv2.COLOR_BGR2LAB), algo, region_size, ruler)
    sp.iterate(iterations)
    sp.enforceLabelConnectivity(25)
    labels = sp.getLabels().astype(np.int32)
    n = sp.getNumberOfSuperpixels()
    boundaries = img.copy()
    boundaries[sp.getLabelContourMask(thick_line=True) > 0] = parse_color(color, 3)
    sums = np.zeros((n, 3), np.float64)
    np.add.at(sums, labels.ravel(), img.reshape(-1, 3))
    counts = np.bincount(labels.ravel(), minlength=n)[:, None]
    means = (sums / np.maximum(counts, 1)).round().astype(np.uint8)
    return Result([boundaries, labels, means[labels]], {"count": int(n)})


@op("segmentation")
def mean_shift(image: Img, spatial_radius: int = 20, color_radius: int = 30, max_level: int = 1) -> Result:
    """Mean-shift filtering: flattens textures into uniform color regions while keeping edges
    (posterize/cartoon look; a good preprocessing step before contours, kmeans_colors or flood_fill).
    Larger radii = flatter regions, slower."""
    out = cv2.pyrMeanShiftFiltering(to_bgr(to_uint8(image)), spatial_radius, color_radius, maxLevel=max_level)
    return Result([out])
