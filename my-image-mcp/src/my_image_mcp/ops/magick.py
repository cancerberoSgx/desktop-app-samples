"""ImageMagick-only operations: color grading, LUTs, dithered quantization,
lens/arc/polar/control-point distortions, seam carving, artistic effects,
contact sheets, perceptual comparison, frames and generated backgrounds.

Registered only when an ImageMagick 7 ``magick`` binary is installed (see
``_magick``); every call runs under a strict security policy.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, InPath, Result
from . import _magick
from ._util import channels, match_to, parse_color, to_uint8

_op = (lambda: op("magick")) if _magick.available() else (lambda: lambda f: f)


def _c(color: str) -> str:
    """User RGB color -> ImageMagick '#rrggbb[aa]'."""
    b, g, r, a = parse_color(color, 4)
    return f"#{r:02x}{g:02x}{b:02x}" + (f"{a:02x}" if a != 255 else "")


def _num(v: float) -> str:
    return f"{float(v):g}"


@_op()
def color_grade(
    image: Img,
    auto_level: bool = False,
    auto_gamma: bool = False,
    black_point: float = 0.0,
    white_point: float = 100.0,
    gamma: float = 1.0,
    brightness: float = 100.0,
    saturation: float = 100.0,
    hue: float = 100.0,
    sigmoidal_contrast: float = 0.0,
    contrast_midpoint: float = 50.0,
    sepia: float = 0.0,
    tint: str | None = None,
    tint_amount: float = 30.0,
) -> Result:
    """Photo-style color grading in one call (applied in this order): auto_level stretches the range,
    auto_gamma balances midtones; levels (black_point/white_point in %, gamma); brightness/saturation/hue
    in % (100 = unchanged; hue 0-200 rotates); sigmoidal_contrast (3-10 typical, negative reduces)
    around contrast_midpoint %, which avoids clipping unlike linear contrast; sepia (% threshold,
    ~80 classic); tint the midtones toward a color by tint_amount %."""
    ops: list[str] = []
    if auto_level:
        ops.append("-auto-level")
    if auto_gamma:
        ops.append("-auto-gamma")
    if (black_point, white_point, gamma) != (0.0, 100.0, 1.0):
        ops += ["-level", f"{_num(black_point)}%,{_num(white_point)}%,{_num(gamma)}"]
    if (brightness, saturation, hue) != (100.0, 100.0, 100.0):
        ops += ["-modulate", f"{_num(brightness)},{_num(saturation)},{_num(hue)}"]
    if sigmoidal_contrast:
        flag = "-sigmoidal-contrast" if sigmoidal_contrast > 0 else "+sigmoidal-contrast"
        ops += [flag, f"{_num(abs(sigmoidal_contrast))}x{_num(contrast_midpoint)}%"]
    if sepia:
        ops += ["-sepia-tone", f"{_num(sepia)}%"]
    if tint:
        ops += ["-fill", _c(tint), "-tint", _num(tint_amount)]
    if not ops:
        return Result([image.copy()], {"note": "no adjustment requested"})
    out, _ = _magick.run([image], lambda i, o: [i[0], *ops, o])
    return Result([out])


@_op()
def apply_lut(image: Img, lut: Img | None = None, cube_file: InPath | None = None, strength: float = 1.0) -> Result:
    """Apply a color lookup table (film emulations, camera looks, grading presets): either a Hald CLUT
    image handle (lut; make an identity one with generate_image(kind='hald_identity'), grade it with
    any color ops, then apply it to other images) or a .cube file (cube_file). strength 0-1 blends
    with the original."""
    if (lut is None) == (cube_file is None):
        raise ValueError("pass exactly one of lut (Hald image handle) or cube_file (.cube path)")
    if cube_file is not None:
        out, _ = _magick.run([image], lambda i, o: [i[0], f"cube:{cube_file}", "-hald-clut", o])
    else:
        out, _ = _magick.run([image, lut], lambda i, o: [i[0], i[1], "-hald-clut", o])
    out = match_to(out, image, resize=False)
    if strength < 1.0:
        out = cv2.addWeighted(out, float(strength), image, 1.0 - float(strength), 0)
    return Result([out])


@_op()
def quantize(
    image: Img,
    colors: int = 16,
    dither: Literal["floyd_steinberg", "riemersma", "none"] = "floyd_steinberg",
    palette: Img | None = None,
    max_report: int = 64,
) -> Result:
    """Reduce to a limited palette with error-diffusion dithering (GIF/retro/print look, smooth
    gradients with few colors). With palette (an image handle, e.g. a strip of swatches), maps to
    exactly those colors (remap) instead of choosing colors. Returns the used colors (RGB hex)."""
    dith = (
        ["+dither"] if dither == "none" else ["-dither", {"floyd_steinberg": "FloydSteinberg"}.get(dither, "Riemersma")]
    )
    if palette is not None:
        out, _ = _magick.run([image, palette], lambda i, o: [i[0], *dith, "-remap", i[1], o])
    else:
        # pick the palette without dithering (rounded to the output depth), then dither onto it:
        # plain "-dither ... -colors N" in an HDRI build can end up with more than N colors
        depth = "16" if image.dtype == np.uint16 else "8"
        pal = ["(", "+clone", "+dither", "-colors", str(int(colors)), "-depth", depth, "-unique-colors"]
        pal += ["-write", "mpr:pal", "+delete", ")"]
        out, _ = _magick.run([image], lambda i, o: [i[0], *pal, *dith, "-remap", "mpr:pal", o])
    out = match_to(out, image, resize=False)
    px = out.reshape(-1, channels(out))[:, :3] if channels(out) > 1 else out.reshape(-1, 1)
    uniq, counts = np.unique(px, axis=0, return_counts=True)
    order = np.argsort(-counts)[:max_report]
    if px.shape[1] == 1:
        used = [f"#{int(v[0]):02x}" * 3 for v in uniq[order]]
    else:
        used = [f"#{int(v[2]):02x}{int(v[1]):02x}{int(v[0]):02x}" for v in uniq[order]]
    return Result([out], {"color_count": int(len(uniq)), "colors": used})


_DISTORT = {
    "barrel": "Barrel",
    "barrel_inverse": "BarrelInverse",
    "arc": "Arc",
    "polar": "Polar",
    "depolar": "DePolar",
    "shepards": "Shepards",
    "scale_rotate_translate": "SRT",
}


@_op()
def distort(
    image: Img,
    method: Literal["barrel", "barrel_inverse", "arc", "polar", "depolar", "shepards", "scale_rotate_translate"],
    args: list[float],
    expand: bool = False,
    background: str = "black",
) -> Result:
    """Non-linear distortions OpenCV lacks. args by method:
    barrel / barrel_inverse: [A, B, C] (+ optional D, X, Y) lens coefficients; correct barrel
    distortion with small values like [0.0, 0.0, -0.05], add it with positive ones.
    arc: [degrees] (+ rotation, top radius, bottom radius) bends the image into an arc (e.g. text).
    polar / depolar: [] or [outer radius] converts to/from polar coordinates (unwrap round objects).
    shepards: control points [x1, y1, x2, y2, ...] moving (x1,y1) to (x2,y2), smooth warp around them.
    scale_rotate_translate: [scale, angle] or [x, y, scale, angle, new_x, new_y].
    expand=True grows the canvas to fit the result; empty areas get background."""
    m = _DISTORT[method]
    flag = "+distort" if expand else "-distort"
    params = ",".join(_num(a) for a in args) if args else "0"
    out, _ = _magick.run(
        [image],
        lambda i, o: [
            i[0],
            "-virtual-pixel",
            "background",
            "-background",
            _c(background),
            flag,
            m,
            params,
            "+repage",
            o,
        ],
    )
    return Result([match_to(out, image, resize=False)])


@_op()
def liquid_rescale(image: Img, width: int, height: int) -> Result:
    """Content-aware resize (seam carving): changes the aspect ratio by removing/adding low-energy
    seams, so people, buildings and other salient objects keep their proportions while sky, water
    or plain background shrink. Slow on large images: resize to ~1000 px first."""
    if "lqr" not in _magick.delegates():
        raise ValueError("this ImageMagick build lacks the 'lqr' (liblqr) delegate needed for seam carving")
    out, _ = _magick.run([image], lambda i, o: [i[0], "-liquid-rescale", f"{int(width)}x{int(height)}!", o])
    return Result([match_to(out, image, resize=False)])


Effect = Literal[
    "charcoal",
    "sketch",
    "oil_paint",
    "kuwahara",
    "swirl",
    "implode",
    "wave",
    "vignette",
    "emboss",
    "solarize",
    "spread",
    "blue_shift",
    "polaroid",
]


def _effect_args(effect: str, s: float) -> list[str]:
    return {
        "charcoal": ["-charcoal", _num(2 * s)],
        "sketch": ["-sketch", f"0x{_num(10 * s)}+120"],
        "oil_paint": ["-paint", str(max(1, round(4 * s)))],
        "kuwahara": ["-kuwahara", _num(3 * s)],
        "swirl": ["-swirl", _num(90 * s)],
        "implode": ["-implode", _num(0.5 * s)],
        "wave": ["-background", "black", "-wave", f"{_num(10 * s)}x150"],
        "vignette": ["-background", "black", "-vignette", f"0x{_num(30 * s)}"],
        "emboss": ["-emboss", _num(s)],
        "solarize": ["-solarize", f"{_num(min(100.0, 50 * s))}%"],
        "spread": ["-spread", _num(5 * s)],
        "blue_shift": ["-blue-shift", _num(1.5 * s)],
        "polaroid": ["-bordercolor", "white", "-background", "black", "+polaroid"],
    }[effect]


@_op()
def artistic_effect(image: Img, effect: Effect, strength: float = 1.0) -> Result:
    """Artistic filters: charcoal, sketch (pencil), oil_paint, kuwahara (painterly, edge-preserving),
    swirl, implode (negative strength explodes), wave (adds height), vignette (dark edges), emboss,
    solarize, spread (jitter), blue_shift (moonlight), polaroid (white frame + shadow, adds alpha).
    strength scales the effect (1 = typical)."""
    out, _ = _magick.run([image], lambda i, o: [i[0], *_effect_args(effect, strength), o])
    if effect != "polaroid":
        out = match_to(out, image, resize=False)
    return Result([out])


@_op()
def montage(
    images: list[Img],
    labels: list[str] | None = None,
    columns: int | None = None,
    tile_size: int = 256,
    spacing: int = 8,
    background: str = "white",
    label_color: str = "black",
    font_size: int = 14,
    shadow: bool = False,
) -> Result:
    """Contact sheet: lay out images in a grid of tile_size boxes (aspect kept), with optional labels
    under each one. Great for comparing variants side by side (one preview call instead of many)."""
    if labels and len(labels) != len(images):
        raise ValueError(f"got {len(labels)} labels for {len(images)} images")
    cols = columns or int(np.ceil(np.sqrt(len(images))))

    def build(paths, o):
        args: list[str] = []
        for k, p in enumerate(paths):
            if labels:
                args += ["-label", _magick.escape_text(labels[k])]
            args.append(p)
        args += ["-tile", f"{cols}x", "-geometry", f"{tile_size}x{tile_size}+{spacing}+{spacing}"]
        args += ["-background", _c(background), "-fill", _c(label_color), "-pointsize", str(font_size)]
        if shadow:
            args.append("-shadow")
        return [*args, o]

    out, _ = _magick.run([to_uint8(i) for i in images], build, command="montage", depth=8)
    return Result([out], {"columns": cols, "rows": int(np.ceil(len(images) / cols))})


@_op()
def frame(
    image: Img,
    style: Literal["border", "bevel_frame", "raise", "sunken", "shadow", "rounded"] = "border",
    size: int = 10,
    color: str = "white",
) -> Result:
    """Decorate an image: border (flat, color), bevel_frame (3-D picture frame in color), raise /
    sunken (3-D button edge), shadow (soft drop shadow, transparent background, adds alpha),
    rounded (rounded corners of radius size, transparent outside, adds alpha)."""
    if style == "rounded":
        img = to_uint8(image)
        bgra = (
            cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA if channels(img) == 1 else cv2.COLOR_BGR2BGRA)
            if channels(img) != 4
            else img.copy()
        )
        h, w = bgra.shape[:2]
        r = max(1, min(size, h // 2, w // 2))
        mask = np.zeros((h, w), np.uint8)
        cv2.rectangle(mask, (r, 0), (w - 1 - r, h - 1), 255, -1)
        cv2.rectangle(mask, (0, r), (w - 1, h - 1 - r), 255, -1)
        for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
            cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
        bgra[:, :, 3] = np.minimum(bgra[:, :, 3], mask)
        return Result([bgra])
    s, c = str(int(size)), _c(color)
    ops = {
        "border": ["-bordercolor", c, "-border", s],
        "bevel_frame": ["-mattecolor", c, "-frame", f"{s}x{s}+{size // 2}+{size // 2}"],
        "raise": ["-raise", s],
        "sunken": ["+raise", s],
        "shadow": [
            "(",
            "+clone",
            "-background",
            "black",
            "-shadow",
            f"60x{_num(size / 2)}+{_num(size / 2)}+{_num(size / 2)}",
            ")",
            "+swap",
            "-background",
            "none",
            "-layers",
            "merge",
            "+repage",
        ],  # fmt: skip
    }[style]
    out, _ = _magick.run([image], lambda i, o: [i[0], *ops, o])
    return Result([out])


_PATTERNS = {
    "checkerboard": "checkerboard",
    "bricks": "bricks",
    "hexagons": "hexagons",
    "crosshatch": "crosshatch45",
    "circles": "circles",
}


@_op()
def generate_image(
    kind: Literal[
        "gradient",
        "radial_gradient",
        "plasma",
        "fractal",
        "checkerboard",
        "bricks",
        "hexagons",
        "crosshatch",
        "circles",
        "hald_identity",
    ] = "gradient",
    width: int = 512,
    height: int = 512,
    color1: str = "white",
    color2: str = "black",
    angle: float = 0.0,
    level: int = 8,
    seed: int = 0,
) -> Result:
    """Generate backgrounds and test images: linear gradient (color1 -> color2, rotated by angle
    degrees; 0 = top to bottom), radial_gradient, plasma (cloudy, between the colors), fractal
    (random colorful plasma), tiled patterns in color2 on color1, or hald_identity: an identity color
    LUT of the given level (image is level^3 px square) to grade and use with apply_lut."""
    size = ["-size", f"{int(width)}x{int(height)}"]
    c1, c2 = _c(color1), _c(color2)
    if kind == "gradient":
        args = [*size, "-define", f"gradient:angle={_num(180 + angle)}", f"gradient:{c1}-{c2}"]
    elif kind == "radial_gradient":
        args = [*size, f"radial-gradient:{c1}-{c2}"]
    elif kind == "plasma":
        args = ["-seed", str(seed), *size, f"plasma:{c1}-{c2}"]
    elif kind == "fractal":
        args = ["-seed", str(seed), *size, "plasma:fractal"]
    elif kind == "hald_identity":
        if not 2 <= level <= 16:
            raise ValueError("level must be 2..16")
        args = [f"hald:{int(level)}"]
    else:
        args = [*size, f"pattern:{_PATTERNS[kind]}", "+level-colors", f"{c2},{c1}"]
    out, _ = _magick.run([], lambda i, o: [*args, "-alpha", "off", o])
    return Result([out])
