"""Loading and saving image files (Pillow).

Opening accepts anything Pillow reads; saving writes PNG or JPEG, picked by
the file extension. Images are edited as RGBA; on save a JPEG is flattened
onto white, and a PNG without any transparent pixel is written as RGB. The
EXIF orientation is applied on load (so phone photos aren't sideways) and
the ICC colour profile is carried over to the saved file.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from app.editor.render import has_alpha

PNG, JPEG = "PNG", "JPEG"
SAVE_FORMATS = {".png": PNG, ".jpg": JPEG, ".jpeg": JPEG}

_OPEN_EXTS = ("png", "jpg", "jpeg", "bmp", "gif", "webp", "tif", "tiff")
OPEN_WILDCARD = (
    "Images|" + ";".join(f"*.{e};*.{e.upper()}" for e in _OPEN_EXTS)
    + "|PNG (*.png)|*.png;*.PNG|JPEG (*.jpg, *.jpeg)|*.jpg;*.jpeg;*.JPG;*.JPEG|All files|*"
)
SAVE_WILDCARD = "PNG (*.png)|*.png|JPEG (*.jpg)|*.jpg"
SAVE_WILDCARD_EXTS = (".png", ".jpg")  # by filter index


@dataclass
class LoadedImage:
    image: Image.Image          # RGBA
    format: Optional[str]       # Pillow's format name, e.g. "JPEG"
    icc_profile: Optional[bytes]


def load_image(path: Path) -> LoadedImage:
    with Image.open(path) as im:
        im.load()
        fmt = im.format
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        try:
            rgba = im.convert("RGBA")
        except ValueError:  # modes Pillow can't convert directly (e.g. 16-bit)
            rgba = im.convert("RGB").convert("RGBA")
    return LoadedImage(rgba, fmt, icc)


def save_format_for(path: Path) -> Optional[str]:
    return SAVE_FORMATS.get(Path(path).suffix.lower())


def with_save_extension(path: Path, filter_index: int = 0) -> Path:
    """`path` itself if it already ends in .png/.jpg/.jpeg, else with the
    extension of the chosen file-dialog filter appended."""
    path = Path(path)
    if save_format_for(path):
        return path
    ext = SAVE_WILDCARD_EXTS[filter_index] if 0 <= filter_index < len(SAVE_WILDCARD_EXTS) else ".png"
    return path.with_name(path.name + ext)


def save_image(image: Image.Image, path: Path, jpeg_quality: int = 90, png_compress_level: int = 6,
               icc_profile: Optional[bytes] = None) -> None:
    """Write `image` (already flattened) to `path`. Written to a temp file
    first and renamed over the target, so a failed save never leaves a
    truncated file behind."""
    path = Path(path)
    fmt = save_format_for(path)
    if fmt is None:
        raise ValueError(f"Can't save as '{path.suffix or path.name}': use .png, .jpg or .jpeg")
    extra = {"icc_profile": icc_profile} if icc_profile else {}
    if fmt == JPEG:
        out = Image.new("RGB", image.size, (255, 255, 255))
        out.paste(image, mask=image.getchannel("A") if image.mode == "RGBA" else None)
        options = dict(quality=max(1, min(100, jpeg_quality)), optimize=True, **extra)
    else:
        out = image if has_alpha(image) else image.convert("RGB")
        options = dict(compress_level=max(0, min(9, png_compress_level)), **extra)

    tmp = path.with_name(f".{path.name}.saving")
    try:
        out.save(tmp, format=fmt, **options)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
