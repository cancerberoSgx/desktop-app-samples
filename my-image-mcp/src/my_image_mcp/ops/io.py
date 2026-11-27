"""Loading, saving and creating images."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, InPath, OutPath, Result
from . import _magick, _vips
from ._util import parse_color, to_bgr, to_gray, to_uint8

_LOAD_FLAGS = {
    "color": cv2.IMREAD_COLOR,
    "grayscale": cv2.IMREAD_GRAYSCALE,
    "unchanged": cv2.IMREAD_UNCHANGED,
}
# Formats OpenCV can't decode (or can't rasterize at a chosen dpi / page).
_NOT_OPENCV = {".heic", ".heif", ".avif", ".jxl", ".svg", ".svgz", ".pdf", ".gif", ".psd", ".psb", ".dng", ".cr2",
               ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf", ".pef", ".srw"}  # fmt: skip


def _apply_mode(img: np.ndarray, mode: str) -> np.ndarray:
    """Make a fallback loader's output match what cv2.imdecode would give for ``mode``."""
    if mode == "unchanged":
        return img
    if img.dtype != np.uint8:
        img = (img / 257).astype(np.uint8) if img.dtype == np.uint16 else to_uint8(img)
    return to_gray(img) if mode == "grayscale" else to_bgr(img)


@op("io")
def load_image(
    path: InPath,
    mode: Literal["color", "grayscale", "unchanged"] = "color",
    page: int = 0,
    dpi: float = 150.0,
) -> Result:
    """Load an image file into memory and return its handle. Reads png, jpg, tiff, webp, bmp, AVIF,
    SVG, GIF and, when ImageMagick is installed, HEIC (iPhone photos), JPEG XL, PDF, PSD and camera RAW.

    'color' gives 3-channel BGR, 'grayscale' 1-channel, 'unchanged' keeps alpha and 16-bit depth.
    EXIF orientation is applied and embedded color profiles (Display P3, Adobe RGB...) are converted
    to sRGB. page selects a PDF page / GIF frame / TIFF page (0-based); dpi sets PDF/SVG resolution.
    Relative paths are resolved against the server's first root directory."""
    ext = Path(path).suffix.lower()
    info: dict = {"path": path, "file_bytes": Path(path).stat().st_size}
    img = None
    # 1) OpenCV: fastest, but ignores ICC profiles, so profiled files go to libvips
    if ext not in _NOT_OPENCV and page == 0 and not _vips.has_icc_profile(path):
        # np.fromfile + imdecode handles non-ASCII paths on Windows, unlike cv2.imread.
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), _LOAD_FLAGS[mode])
        if img is not None:
            info["loader"] = "opencv"
    errors = []
    # 2) libvips: AVIF, SVG, GIF/TIFF pages, ICC conversion
    if img is None and _vips.AVAILABLE:
        try:
            img, extra = _vips.load(path, page, dpi)
            info.update(extra)
        except Exception as e:
            errors.append(f"libvips: {str(e).strip().splitlines()[0]}")
    # 3) ImageMagick: HEIC, JPEG XL, PDF, PSD, RAW
    if img is None and _magick.available():
        try:
            img = _magick.load(path, page, dpi)
            info["loader"] = "imagemagick"
        except Exception as e:
            errors.append(f"imagemagick: {e}")
    if img is None:
        hint = "" if _magick.available() else " Installing ImageMagick 7 adds HEIC, JPEG XL, PDF, PSD and RAW support."
        raise ValueError(f"Could not decode image file {path}. {'; '.join(errors)}{hint}".strip())
    if info.get("loader") != "opencv":
        img = _apply_mode(img, mode)
    return Result([img], info)


_VIPS_SAVE = {".gif", ".avif"}
_MAGICK_SAVE = {".heic", ".heif", ".jxl", ".pdf", ".psd"}


@op("io")
def save_image(image: Img, path: OutPath, quality: int = 95) -> Result:
    """Save an image to a file; the format follows the extension: .png, .jpg, .webp, .tif, .bmp,
    .avif, .gif and, with ImageMagick installed, .heic, .jxl, .pdf, .psd.

    quality (0-100) applies to lossy formats; png uses lossless compression. Parent folders are created.
    """
    ext = Path(path).suffix.lower() or ".png"
    lossless_float = (".tif", ".tiff", ".exr", ".hdr")
    if image.dtype not in (np.uint8, np.uint16) and ext not in lossless_float:
        raise ValueError(f"{image.dtype} images can't be saved as {ext}; normalize to uint8 first (normalize op).")
    if ext in _MAGICK_SAVE:
        if not _magick.available():
            raise ValueError(f"saving {ext} needs ImageMagick 7 installed")
        _magick.save(image, path, quality)
        return Result([], {"path": path, "file_bytes": Path(path).stat().st_size, "writer": "imagemagick"})
    if ext in _VIPS_SAVE and _vips.AVAILABLE:
        img8 = image if image.dtype == np.uint8 else (image / 257).astype(np.uint8)
        v = _vips.to_vips(img8)
        if ext == ".gif":
            v.gifsave(path)
        else:
            v.heifsave(path, Q=int(quality), compression="av1")
        return Result([], {"path": path, "file_bytes": Path(path).stat().st_size, "writer": "libvips"})
    params: list[int] = []
    if ext in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif ext in (".webp", ".avif"):
        params = [cv2.IMWRITE_WEBP_QUALITY if ext == ".webp" else cv2.IMWRITE_AVIF_QUALITY, int(quality)]
    ok, buf = cv2.imencode(ext, image, params)
    if not ok:
        raise ValueError(f"Could not encode image as {ext}")
    buf.tofile(path)
    return Result([], {"path": path, "file_bytes": int(buf.size)})


@op("io")
def create_image(
    width: int,
    height: int,
    channels: Literal[1, 3, 4] = 3,
    color: str = "#000000",
    dtype: Literal["uint8", "uint16", "float32"] = "uint8",
) -> Result:
    """Create a new blank image filled with a color ('#RRGGBB', a name, or 'R,G,B')."""
    img = np.zeros((height, width) if channels == 1 else (height, width, channels), dtype=dtype)
    img[:] = parse_color(color, channels) if channels > 1 else parse_color(color, 1)[0]
    return Result([img])
