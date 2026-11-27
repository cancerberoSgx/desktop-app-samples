"""ImageMagick bridge. ImageMagick is *not* bundled (there is no pip wheel and
bundling it per-OS is costly), so it is optional: the `magick` tools are only
registered when an ImageMagick 7 ``magick`` binary is found (on PATH or via
``MY_IMAGE_MCP_MAGICK=/path/to/magick``; ``MY_IMAGE_MCP_MAGICK=off`` disables it).

Security: ImageMagick can read files and URLs on its own (``@file``, ``url:``,
``msl:``, ``text:``, ...), which would bypass the path sandbox. Every call
therefore runs with our ``magick_config/policy.xml`` (an allowlist of formats,
``@`` indirection disabled, resource limits) and only ever receives
arguments built by the curated ops, never raw model input. Images are passed
as PNG files in a private temp dir.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from ._util import to_uint8

POLICY_DIR = Path(__file__).with_name("magick_config")
TIMEOUT = 120


@functools.cache
def binary() -> str | None:
    env = os.environ.get("MY_IMAGE_MCP_MAGICK", "").strip()
    if env.lower() in ("off", "0", "false", "no"):
        return None
    exe = env or shutil.which("magick")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if re.search(r"ImageMagick 7\.", out) else None


@functools.cache
def version() -> str | None:
    exe = binary()
    if not exe:
        return None
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10).stdout
    m = re.search(r"ImageMagick (\S+)", out)
    return m.group(1) if m else None


@functools.cache
def delegates() -> frozenset[str]:
    exe = binary()
    if not exe:
        return frozenset()
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10).stdout
    m = re.search(r"Delegates[^:]*:(.*)", out)
    return frozenset(m.group(1).split()) if m else frozenset()


def available() -> bool:
    return binary() is not None


def escape_text(text: str) -> str:
    """Neutralise ImageMagick's percent escapes and @file indirection in text arguments."""
    text = text.replace("%", "%%")
    return "\\@" + text[1:] if text.startswith("@") else text


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["MAGICK_CONFIGURE_PATH"] = str(POLICY_DIR)
    return env


def _write(img: np.ndarray, path: Path) -> None:
    if img.dtype not in (np.uint8, np.uint16):
        img = to_uint8(img)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("could not encode image for ImageMagick")
    buf.tofile(str(path))


def _read(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("ImageMagick produced no readable image")
    return img


class MagickError(ValueError):
    pass


def run_raw(args: list[str], ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    exe = binary()
    if exe is None:
        raise MagickError("ImageMagick 7 ('magick') is not installed; e.g. brew/apt/choco install imagemagick")
    try:
        proc = subprocess.run([exe, *args], capture_output=True, env=_env(), timeout=TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise MagickError(f"ImageMagick timed out after {TIMEOUT}s") from e
    if proc.returncode not in ok_codes:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()
        raise MagickError(f"ImageMagick failed: {msg[-1] if msg else f'exit code {proc.returncode}'}")
    return proc


def run(
    inputs: list[np.ndarray],
    build_args,
    *,
    command: str | None = None,
    depth: int | None = None,
    ok_codes: tuple[int, ...] = (0,),
) -> tuple[np.ndarray | None, subprocess.CompletedProcess]:
    """Write ``inputs`` as PNGs, call ``build_args(input_paths, output_path)``
    to get the argument list, run ``magick [command] ...`` and read the output.

    ``depth`` defaults to the first input's bit depth (8 or 16)."""
    with tempfile.TemporaryDirectory(prefix="my-image-mcp-") as tmp:
        tmpdir = Path(tmp)
        paths = []
        for i, img in enumerate(inputs):
            p = tmpdir / f"in{i}.png"
            _write(img, p)
            paths.append(f"png:{p}")
        out = tmpdir / "out.png"
        if depth is None:
            depth = 16 if inputs and inputs[0].dtype == np.uint16 else 8
        args = [*([command] if command else []), *build_args(paths, f"png:{out}")]
        # bit depth for the output, placed right before the output filename
        args = [*args[:-1], "-depth", str(depth), args[-1]]
        proc = run_raw(args, ok_codes)
        if not out.exists():
            return None, proc
        img = _read(out)
        # ImageMagick writes all-gray results as 1-channel PNGs: restore the input's color layout
        if img.ndim == 2 and (not inputs or inputs[0].ndim == 3):
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img, proc


def load(path: str, page: int = 0, dpi: float = 150.0) -> np.ndarray:
    """Read any format ImageMagick (and its delegates) supports, auto-oriented,
    keeping the ICC profile in the intermediate PNG so libvips can convert it."""
    with tempfile.TemporaryDirectory(prefix="my-image-mcp-") as tmp:
        out = Path(tmp) / "out.png"
        run_raw(["-density", str(dpi), f"{path}[{int(page)}]", "-auto-orient", f"png:{out}"])
        from . import _vips  # local import: _vips is optional

        if _vips.AVAILABLE:
            img, _ = _vips.load(str(out))
            return img
        return _read(out)


def save(img: np.ndarray, path: str, quality: int) -> None:
    run([img], lambda i, o: [i[0], "-quality", str(quality), path])
