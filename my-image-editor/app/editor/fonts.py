"""Fonts for text shapes - always the bundled DejaVu files in app/fonts.

Text is rendered by Pillow from these exact files both on the canvas and in
the saved image, so a picture looks the same whichever OS edited or saved it
(system fonts differ per platform). If a file is missing (e.g. a broken
install), Pillow's built-in scalable font is used instead.
"""
from functools import lru_cache
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont

from app.db.paths import project_root

SANS, SERIF, MONO = "Sans", "Serif", "Mono"
FAMILIES = (SANS, SERIF, MONO)
DEFAULT_FAMILY = SANS

# (family, bold, italic) -> file in app/fonts
_FILES = {
    (SANS, False, False): "DejaVuSans.ttf",
    (SANS, True, False): "DejaVuSans-Bold.ttf",
    (SANS, False, True): "DejaVuSans-Oblique.ttf",
    (SANS, True, True): "DejaVuSans-BoldOblique.ttf",
    (SERIF, False, False): "DejaVuSerif.ttf",
    (SERIF, True, False): "DejaVuSerif-Bold.ttf",
    (SERIF, False, True): "DejaVuSerif-Italic.ttf",
    (SERIF, True, True): "DejaVuSerif-BoldItalic.ttf",
    (MONO, False, False): "DejaVuSansMono.ttf",
    (MONO, True, False): "DejaVuSansMono-Bold.ttf",
    (MONO, False, True): "DejaVuSansMono-Oblique.ttf",
    (MONO, True, True): "DejaVuSansMono-BoldOblique.ttf",
}

LINE_SPACING = 0.25  # extra space between lines, as a fraction of the font size


def fonts_dir():
    return project_root() / "app" / "fonts"


def normalize_family(family: str) -> str:
    return family if family in FAMILIES else DEFAULT_FAMILY


@lru_cache(maxsize=128)
def get_font(family: str, size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    size = max(1, int(round(size)))
    path = fonts_dir() / _FILES[(normalize_family(family), bool(bold), bool(italic))]
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size)


_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))


def line_spacing(size: float) -> int:
    return max(0, int(round(size * LINE_SPACING)))


def text_bbox(text: str, family: str, size: float, bold: bool, italic: bool) -> Tuple[int, int, int, int]:
    """Ink box of `text` drawn at (0, 0) with anchor "la" (left/ascender) -
    may start slightly right of / below 0, never above the line box."""
    font = get_font(family, int(round(size)), bold, italic)
    if not text:
        return (0, 0, 0, int(round(size)))
    return _MEASURE.multiline_textbbox((0, 0), text, font=font, spacing=line_spacing(size), anchor="la")
