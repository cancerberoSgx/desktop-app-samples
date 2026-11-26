"""Loading, saving and creating images."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, InPath, OutPath, Result
from ._util import parse_color

_LOAD_FLAGS = {
    "color": cv2.IMREAD_COLOR,
    "grayscale": cv2.IMREAD_GRAYSCALE,
    "unchanged": cv2.IMREAD_UNCHANGED,
}


@op("io")
def load_image(path: InPath, mode: Literal["color", "grayscale", "unchanged"] = "color") -> Result:
    """Load an image file (png, jpg, tiff, webp, bmp, ...) into memory and return its handle.

    'color' gives 3-channel BGR, 'grayscale' 1-channel, 'unchanged' keeps alpha and 16-bit depth.
    Relative paths are resolved against the server's first root directory.
    """
    # np.fromfile + imdecode handles non-ASCII paths on Windows, unlike cv2.imread.
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, _LOAD_FLAGS[mode])
    if img is None:
        raise ValueError(f"Could not decode image file {path}")
    return Result([img], {"path": path, "file_bytes": int(data.size)})


@op("io")
def save_image(image: Img, path: OutPath, quality: int = 95) -> Result:
    """Save an image to a file; the format follows the extension (.png, .jpg, .webp, .tif, .bmp).

    quality (0-100) applies to jpg/webp; png uses lossless compression. Parent folders are created.
    """
    ext = Path(path).suffix.lower() or ".png"
    params: list[int] = []
    if ext in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif ext == ".webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, int(quality)]
    if image.dtype not in (np.uint8, np.uint16) and ext not in (".tif", ".tiff", ".exr", ".hdr"):
        raise ValueError(f"{image.dtype} images can't be saved as {ext}; normalize to uint8 first (normalize op).")
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
