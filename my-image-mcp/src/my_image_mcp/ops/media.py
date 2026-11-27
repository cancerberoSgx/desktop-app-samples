"""libvips-backed tools OpenCV lacks: real-font text, Photoshop-style blend
modes, content-aware cropping and file metadata.

Registered only when libvips is available (it ships with the ``pyvips[binary]``
wheel on Linux, macOS and Windows).
"""

from __future__ import annotations

import html
from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, InPath, Result
from . import _magick, _vips
from ._util import channels, ensure_color_for, parse_color, to_bgr, to_uint8

_op = (lambda: op("media")) if _vips.AVAILABLE else (lambda: lambda f: f)


def _rgb_hex(color: str) -> str:
    b, g, r = parse_color(color, 3)
    return f"#{r:02x}{g:02x}{b:02x}"


def _alpha_blend(base: np.ndarray, rgba: np.ndarray, x: int, y: int, opacity: float = 1.0) -> np.ndarray:
    """Alpha-composite a BGRA uint8 overlay onto a copy of base at (x, y), clipped to bounds."""
    out = base.copy()
    h, w = out.shape[:2]
    oh, ow = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + ow), min(h, y + oh)
    if x0 >= x1 or y0 >= y1:
        return out
    src = rgba[y0 - y : y1 - y, x0 - x : x1 - x].astype(np.float32)
    a = src[:, :, 3:4] / 255.0 * opacity
    scale = 257.0 if out.dtype == np.uint16 else 1.0
    region = out[y0:y1, x0:x1].astype(np.float32)
    ch = channels(out)
    if ch == 1:
        fg = cv2.cvtColor(src[:, :, :3].astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32) * scale
        region = region * (1 - a[:, :, 0]) + fg * a[:, :, 0]
    else:
        region[:, :, :3] = region[:, :, :3] * (1 - a) + src[:, :, :3] * scale * a
        if ch == 4:
            region[:, :, 3:4] = np.maximum(region[:, :, 3:4], a * 255 * scale)
    out[y0:y1, x0:x1] = np.clip(region, 0, 65535 if out.dtype == np.uint16 else 255).astype(out.dtype)
    return out


@_op()
def render_text(
    image: Img,
    text: str,
    x: int = 10,
    y: int = 10,
    font: str = "Sans",
    size: float = 32.0,
    color: str = "white",
    bold: bool = False,
    italic: bool = False,
    wrap_width: int | None = None,
    align: Literal["left", "centre", "right"] = "left",
    background: str | None = None,
    padding: int = 6,
    opacity: float = 1.0,
    markup: bool = False,
    font_file: InPath | None = None,
) -> Result:
    """Render high-quality text with real fonts: any Unicode (accents, CJK, emoji), anti-aliased,
    word-wrapped to wrap_width px. (x, y) is the top-left of the text box. font is a family name
    ("Sans", "Serif", "Monospace", "DejaVu Sans", "Helvetica", ...) or use font_file for a .ttf/.otf.
    size is in px. markup=True accepts Pango markup (<b>, <i>, <span foreground='#f00'>...).
    Optional background box color. Returns the text box [x, y, w, h]."""
    vips = _vips.require()
    body = text if markup else html.escape(text)
    attrs = f'foreground="{_rgb_hex(color)}"'
    if bold:
        attrs += ' weight="bold"'
    if italic:
        attrs += ' style="italic"'
    kwargs: dict = {"font": f"{font} {size}", "rgba": True, "dpi": 72}
    kwargs["align"] = {"left": "low", "centre": "centre", "right": "high"}[align]
    if wrap_width:
        kwargs["width"] = int(wrap_width)
    if font_file:
        kwargs["fontfile"] = font_file
    try:
        t = vips.Image.text(f"<span {attrs}>{body}</span>", **kwargs)
    except vips.Error as e:
        raise ValueError(f"could not render text: {str(e).strip().splitlines()[0]}") from e
    rgba = cv2.cvtColor(t.numpy(), cv2.COLOR_RGBA2BGRA)
    out = ensure_color_for(image, color if background is None else background)
    if background is not None:
        th, tw = rgba.shape[:2]
        box = np.zeros((th + 2 * padding, tw + 2 * padding, 4), np.uint8)
        box[:] = (*parse_color(background, 3), 255)
        box = _alpha_blend(box, rgba, padding, padding)
        rgba, x, y = box, x - padding, y - padding
    out = _alpha_blend(out, rgba, x, y, opacity)
    h, w = rgba.shape[:2]
    return Result([out], {"box": [x, y, w, h]})


BlendMode = Literal[
    "over",
    "multiply",
    "screen",
    "overlay",
    "darken",
    "lighten",
    "colour_dodge",
    "colour_burn",
    "hard_light",
    "soft_light",
    "difference",
    "exclusion",
    "add",
    "saturate",
    "dest_in",
    "dest_out",
    "atop",
    "xor",
]


@_op()
def blend_mode(
    base: Img,
    top: Img,
    mode: BlendMode = "multiply",
    x: int = 0,
    y: int = 0,
    opacity: float = 1.0,
) -> Result:
    """Composite top onto base at (x, y) with a Photoshop-style blend mode (multiply darkens,
    screen lightens, overlay/soft_light add contrast, difference highlights changes, dest_in uses
    top's alpha as a mask...). Respects alpha; opacity scales top. Output has base's size and channels."""
    b8, t8 = to_uint8(base), to_uint8(top)
    bv = _vips.to_vips(to_bgr(b8) if channels(b8) == 1 else b8)
    tv = _vips.to_vips(to_bgr(t8) if channels(t8) == 1 else t8)
    if not bv.hasalpha():
        bv = bv.bandjoin(255)
    if not tv.hasalpha():
        tv = tv.bandjoin(255)
    if opacity < 1.0:
        tv = tv.extract_band(0, n=3).bandjoin((tv.extract_band(3) * opacity).cast("uchar"))
    out = bv.composite2(tv, mode, x=x, y=y).cast("uchar")
    arr = _vips.from_vips(out)
    if channels(base) == 1:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
    elif channels(base) == 3:
        arr = arr[:, :, :3].copy()
    return Result([arr])


@_op()
def smart_crop(
    image: Img,
    width: int,
    height: int,
    focus: Literal["attention", "entropy", "centre", "low", "high"] = "attention",
) -> Result:
    """Crop to width x height keeping the most interesting part: attention = salient features
    (skin tones, saturated colors, edges; good for thumbnails of people/products), entropy = most
    detail, centre = plain center crop. Resize first if the image is much larger than the target.
    Returns the crop box [x, y, w, h] in input coordinates."""
    h, w = image.shape[:2]
    if width > w or height > h:
        raise ValueError(f"crop {width}x{height} is larger than the {w}x{h} image")
    v = _vips.to_vips(to_uint8(image))
    cropped = v.smartcrop(width, height, interesting=focus)
    x0, y0 = -cropped.xoffset, -cropped.yoffset
    return Result([image[y0 : y0 + height, x0 : x0 + width].copy()], {"box": [x0, y0, width, height]})


_KEEP_FIELDS = ("width", "height", "bands", "format", "interpretation", "n-pages", "orientation", "xres", "yres")


@_op()
def image_metadata(path: InPath, max_value_length: int = 200) -> Result:
    """Read a file's metadata without loading pixels: size, pages/frames, color profile, DPI, EXIF
    (camera, lens, exposure, date, GPS, orientation), XMP/IPTC presence. No image output."""
    vips = _vips.require()
    data: dict = {"path": path}
    try:
        v = vips.Image.new_from_file(path, access="sequential")
    except vips.Error:
        v = None
    if v is not None:
        data["loader"] = _vips.loader_name(v)
        for f in _KEEP_FIELDS:
            if v.get_typeof(f):
                val = v.get(f)
                data[f.replace("-", "_")] = round(val * 25.4, 1) if f in ("xres", "yres") else val
        if "xres" in data:  # libvips stores pixels/mm
            data["dpi"] = [data.pop("xres"), data.pop("yres")]
        data["icc_profile"] = bool(v.get_typeof("icc-profile-data"))
        data["xmp"] = bool(v.get_typeof("xmp-data"))
        data["iptc"] = bool(v.get_typeof("iptc-data"))
        exif = {}
        for f in v.get_fields():
            if f.startswith("exif-ifd") and not f.endswith("-data"):
                val = str(v.get(f))
                exif[f.split("-", 2)[2]] = val[:max_value_length]
        data["exif"] = exif
    elif _magick.available():
        proc = _magick.run_raw(["identify", "-format", "%[*]", f"{path}[0]"])
        props = {}
        for line in proc.stdout.decode(errors="replace").splitlines():
            k, sep, val = line.partition("=")
            if sep:
                props[k.strip()] = val.strip()[:max_value_length]
        data.update({"loader": "imagemagick", "properties": props})
    else:
        raise ValueError(f"cannot read metadata of {path}")
    return Result([], data)
