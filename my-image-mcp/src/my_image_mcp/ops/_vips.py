"""libvips bridge (pyvips). Ships as a prebuilt wheel (``pyvips[binary]``) on
Linux, macOS and Windows, so it is a regular dependency; still, everything
degrades gracefully if libvips can't be loaded.

Images cross the bridge as numpy arrays in OpenCV layout (BGR / BGRA / gray).
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

try:  # pragma: no cover - depends on the platform's wheel
    import pyvips

    AVAILABLE = True
    VERSION = f"{pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}"
except Exception:  # ImportError, or OSError when no libvips is found
    pyvips = None
    AVAILABLE = False
    VERSION = None


def require() -> Any:
    if not AVAILABLE:
        raise ValueError("libvips is not available (pip install 'pyvips[binary]')")
    return pyvips


def to_vips(img: np.ndarray):
    """numpy (BGR/BGRA/gray, uint8/uint16/float32) -> pyvips image (RGB/RGBA/b-w)."""
    vips = require()
    arr = img
    if arr.ndim == 3 and arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
    v = vips.Image.new_from_array(np.ascontiguousarray(arr))
    color = arr.ndim == 3 and arr.shape[2] >= 3
    if arr.dtype == np.uint16:
        interp = "rgb16" if color else "grey16"
    else:
        interp = "srgb" if color else "b-w"
    return v.copy(interpretation=interp)


def from_vips(v) -> np.ndarray:
    """pyvips image -> numpy in OpenCV layout. Gray+alpha becomes BGRA."""
    if v.bands == 2:
        v = v.colourspace("rgb16" if v.format == "ushort" else "srgb")
    if v.bands > 4:
        v = v.extract_band(0, n=4)
    arr = v.numpy()
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    if arr.ndim == 3 and arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA)
    return np.ascontiguousarray(arr)


def load(path: str, page: int = 0, dpi: float = 150.0, to_srgb: bool = True) -> tuple[np.ndarray, dict]:
    """Load any format libvips knows (AVIF, SVG, GIF/TIFF pages, ...), apply
    EXIF orientation and convert an embedded ICC profile to sRGB."""
    vips = require()
    loader = loader_name(vips.Image.new_from_file(path, access="sequential"))  # header only
    kwargs: dict[str, Any] = {}
    if page and any(k in loader for k in ("gif", "tiff", "heif", "webp", "pdf")):
        kwargs["page"] = page
    if "svg" in loader or "pdf" in loader:
        kwargs["dpi"] = dpi
    v = vips.Image.new_from_file(path, **kwargs)
    info: dict[str, Any] = {"loader": f"libvips ({loader})"}
    pages = v.get("n-pages") if v.get_typeof("n-pages") else 1
    if pages > 1:
        info["pages"] = pages
    v = v.autorot()
    if to_srgb and v.get_typeof("icc-profile-data"):
        depth = 16 if v.format == "ushort" else 8
        try:
            v = v.icc_transform("srgb", embedded=True, depth=depth)
            info["icc_converted_to_srgb"] = True
        except vips.Error:
            pass  # broken profile: keep the raw values
    return from_vips(v), info


def loader_name(v) -> str:
    """e.g. 'pngload', 'heifload', 'svgload' (set by every libvips loader)."""
    return v.get("vips-loader") if v.get_typeof("vips-loader") else ""


def has_icc_profile(path: str) -> bool:
    """Cheap header-only check."""
    if not AVAILABLE:
        return False
    try:
        return bool(pyvips.Image.new_from_file(path, access="sequential").get_typeof("icc-profile-data"))
    except Exception:
        return False
