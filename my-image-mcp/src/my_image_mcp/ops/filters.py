"""Smoothing, sharpening, denoising, edges and custom convolution."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import channels, odd, to_gray


@op("filters")
def blur(
    image: Img,
    method: Literal["gaussian", "median", "box", "bilateral"] = "gaussian",
    ksize: int = 5,
    sigma: float = 0.0,
    sigma_color: float = 75.0,
    sigma_space: float = 75.0,
) -> Result:
    """Smooth an image. gaussian: general purpose; median: removes salt-and-pepper noise;
    box: plain average; bilateral: edge-preserving (uses sigma_color/sigma_space, ksize = diameter).
    ksize is forced odd."""
    k = odd(ksize)
    if method == "gaussian":
        out = cv2.GaussianBlur(image, (k, k), sigma)
    elif method == "median":
        out = cv2.medianBlur(image, k)
    elif method == "box":
        out = cv2.blur(image, (k, k))
    else:
        out = cv2.bilateralFilter(image, k, sigma_color, sigma_space)
    return Result([out])


@op("filters")
def sharpen(image: Img, amount: float = 1.0, sigma: float = 1.0, threshold: int = 0) -> Result:
    """Unsharp mask: image + amount * (image - blurred). threshold skips low-contrast areas (0 = off)."""
    blurred = cv2.GaussianBlur(image, (0, 0), sigma)
    sharp = cv2.addWeighted(image, 1.0 + amount, blurred, -amount, 0)
    if threshold > 0:
        low = cv2.absdiff(image, blurred) < threshold
        sharp = np.where(low, image, sharp)
    return Result([sharp])


@op("filters")
def denoise(image: Img, strength: float = 10.0, template_size: int = 7, search_size: int = 21) -> Result:
    """Non-local means denoising (8-bit images). Higher strength removes more noise but also detail."""
    if image.dtype != np.uint8:
        raise ValueError("denoise needs an 8-bit image (use normalize first)")
    if channels(image) == 3:
        out = cv2.fastNlMeansDenoisingColored(image, None, strength, strength, template_size, search_size)
    else:
        out = cv2.fastNlMeansDenoising(to_gray(image), None, strength, template_size, search_size)
    return Result([out])


@op("filters")
def canny(
    image: Img,
    low: float = 100,
    high: float = 200,
    auto: bool = False,
    aperture: Literal[3, 5, 7] = 3,
    l2_gradient: bool = False,
) -> Result:
    """Canny edge detection -> binary edge map. auto=True derives thresholds from the median
    intensity (ignores low/high). Blur first for noisy images."""
    gray = to_gray(image)
    if gray.dtype != np.uint8:
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    if auto:
        med = float(np.median(gray))
        low, high = max(0.0, 0.66 * med), min(255.0, 1.33 * med)
    edges = cv2.Canny(gray, low, high, apertureSize=aperture, L2gradient=l2_gradient)
    return Result(
        [edges], {"low": low, "high": high, "edge_ratio": round(float(np.count_nonzero(edges)) / edges.size, 4)}
    )


@op("filters")
def gradient(
    image: Img,
    method: Literal["sobel", "scharr", "laplacian"] = "sobel",
    direction: Literal["x", "y", "magnitude"] = "magnitude",
    ksize: int = 3,
    keep_float: bool = False,
) -> Result:
    """Image derivatives. Returns |gradient| scaled to 8-bit unless keep_float=True (signed float32)."""
    gray = to_gray(image).astype(np.float32)
    if method == "laplacian":
        g = cv2.Laplacian(gray, cv2.CV_32F, ksize=odd(ksize))
    else:

        def d(dx: int, dy: int) -> np.ndarray:
            if method == "scharr":
                return cv2.Scharr(gray, cv2.CV_32F, dx, dy)
            return cv2.Sobel(gray, cv2.CV_32F, dx, dy, ksize=odd(ksize))

        if direction == "x":
            g = d(1, 0)
        elif direction == "y":
            g = d(0, 1)
        else:
            g = cv2.magnitude(d(1, 0), d(0, 1))
    if keep_float:
        return Result([g])
    return Result([cv2.convertScaleAbs(g)])


@op("filters")
def convolve(image: Img, kernel: list[list[float]], normalize_kernel: bool = False, delta: float = 0.0) -> Result:
    """Apply a custom 2D convolution kernel (cv2.filter2D), e.g. [[0,-1,0],[-1,5,-1],[0,-1,0]] to sharpen.
    normalize_kernel divides by the kernel sum."""
    k = np.array(kernel, dtype=np.float32)
    if k.ndim != 2:
        raise ValueError("kernel must be a 2D list")
    if normalize_kernel and abs(float(k.sum())) > 1e-9:
        k = k / k.sum()
    return Result([cv2.filter2D(image, -1, k, delta=delta)])
