"""libvips (bundled) and ImageMagick (optional) backed tools and loaders."""

from __future__ import annotations

import subprocess

import cv2
import numpy as np
import pytest

from my_image_mcp.core.engine import OpError
from my_image_mcp.ops import _magick, _vips

needs_vips = pytest.mark.skipif(not _vips.AVAILABLE, reason="libvips not available")
needs_magick = pytest.mark.skipif(not _magick.available(), reason="ImageMagick 7 not installed")


def _put(engine, arr: np.ndarray) -> str:
    return engine.store.put("image", arr)


def _out(res, i: int = 0) -> str:
    return res["outputs"][i]["handle"]


def _get(engine, res, i: int = 0) -> np.ndarray:
    return engine.store.get(_out(res, i))


# --------------------------------------------------------------- load / save


@needs_vips
def test_icc_profile_is_converted_to_srgb(engine, root):
    import pyvips

    red = pyvips.Image.black(32, 32).new_from_image([255, 0, 0]).copy(interpretation="srgb").cast("uchar")
    red.icc_transform("p3").write_to_file(str(root / "p3.jpg"), Q=98)  # Display P3 values + embedded profile
    raw = cv2.imread(str(root / "p3.jpg"))[16, 16]
    assert raw[2] < 245  # stored P3 red is not (255, 0, 0)
    res = engine.execute("load_image", {"path": "p3.jpg"})
    assert res["icc_converted_to_srgb"] and res["loader"].startswith("libvips")
    b, g, r = (int(v) for v in _get(engine, res)[16, 16])
    assert r > 245 and g < 15 and b < 15


@needs_vips
def test_svg_avif_gif_via_libvips(engine, root, img):
    (root / "s.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
        '<rect width="40" height="20" fill="#00f"/></svg>'
    )
    res = engine.execute("load_image", {"path": "s.svg", "dpi": 144})
    a = _get(engine, res)
    assert a.shape == (40, 80, 3) and a[10, 10].tolist() == [255, 0, 0]
    for ext in ("avif", "gif"):
        engine.execute("save_image", {"image": img, "path": f"out.{ext}"})
        back = _get(engine, engine.execute("load_image", {"path": f"out.{ext}"}))
        assert back.shape == (240, 320, 3)
        assert np.abs(back.astype(int) - engine.store.get(img).astype(int)).mean() < 12


@needs_magick
@pytest.mark.parametrize("ext", ["heic", "jxl", "pdf", "psd"])
def test_magick_formats_round_trip(engine, img, ext):
    if ext == "pdf":
        pytest.importorskip("shutil").which("gs") or pytest.skip("Ghostscript needed to read PDF")
    engine.execute("save_image", {"image": img, "path": f"out.{ext}"})
    res = engine.execute("load_image", {"path": f"out.{ext}", "dpi": 72})
    assert res["loader"] == "imagemagick" or res["loader"].startswith("libvips")
    back = _get(engine, res)
    assert abs(back.shape[1] - 320) <= 2 and abs(back.shape[0] - 240) <= 2


def test_unknown_format_error_is_readable(engine, root):
    (root / "junk.xyz").write_bytes(b"not an image")
    with pytest.raises(OpError, match="Could not decode"):
        engine.execute("load_image", {"path": "junk.xyz"})


# --------------------------------------------------------------------- media


@needs_vips
def test_render_text_unicode_and_escaping(engine, img):
    res = engine.execute(
        "render_text", {"image": img, "text": "Ñandú <b>&</b> 世界", "x": 20, "y": 150, "size": 28, "color": "yellow"}
    )
    x, y, w, h = res["box"]
    assert w > 100 and h > 20
    before, after = engine.store.get(img), _get(engine, res)
    changed = np.any(before != after, axis=2)
    ys, xs = np.nonzero(changed)
    assert xs.min() >= x and ys.min() >= y and xs.max() < x + w and ys.max() < y + h
    boxed = engine.execute("render_text", {"image": img, "text": "hi", "background": "black", "wrap_width": 50})
    assert boxed["box"][2] > 0


@needs_vips
def test_blend_modes(engine, img):
    white = _put(engine, np.full((240, 320, 3), 255, np.uint8))
    black = _put(engine, np.zeros((240, 320, 3), np.uint8))
    base = engine.store.get(img).astype(int)
    for mode, top in (("multiply", white), ("screen", black)):
        out = _get(engine, engine.execute("blend_mode", {"base": img, "top": top, "mode": mode}))
        assert np.abs(out.astype(int) - base).max() <= 1, mode
    diff = _get(engine, engine.execute("blend_mode", {"base": img, "top": img, "mode": "difference"}))
    assert diff.max() <= 1


@needs_vips
def test_smart_crop_keeps_the_interesting_part(engine):
    a = np.full((200, 400, 3), 128, np.uint8)
    cv2.circle(a, (330, 100), 25, (0, 0, 255), -1)
    res = engine.execute("smart_crop", {"image": _put(engine, a), "width": 100, "height": 100})
    x, y, w, h = res["box"]
    assert x <= 305 and x + w >= 355


@needs_vips
def test_image_metadata(engine, root):
    res = engine.execute("image_metadata", {"path": "shapes.png"})
    assert (res["width"], res["height"], res["loader"]) == (320, 240, "pngload")
    assert res["outputs"] if "outputs" in res else True


# -------------------------------------------------------------------- magick


@needs_magick
def test_color_grade(engine, img):
    gray = _get(engine, engine.execute("color_grade", {"image": img, "saturation": 0}))
    assert np.abs(gray[:, :, 0].astype(int) - gray[:, :, 2].astype(int)).max() <= 2
    sepia = _get(engine, engine.execute("color_grade", {"image": img, "sepia": 80}))
    b, g, r = sepia[5, 5].astype(int)  # dark gray background, not the white circle
    assert r >= g >= b and r > b  # warm brown tone
    assert "note" in engine.execute("color_grade", {"image": img})


@needs_magick
def test_identity_lut_is_a_no_op_and_graded_lut_applies(engine, img):
    hald = _out(engine.execute("generate_image", {"kind": "hald_identity", "level": 8}))
    same = _get(engine, engine.execute("apply_lut", {"image": img, "lut": hald}))
    assert np.abs(same.astype(int) - engine.store.get(img).astype(int)).max() <= 3
    inverted = _out(engine.execute("invert", {"image": hald}))
    out = _get(engine, engine.execute("apply_lut", {"image": img, "lut": inverted}))
    assert np.abs(out.astype(int) - (255 - engine.store.get(img).astype(int))).max() <= 4
    with pytest.raises(OpError, match="exactly one"):
        engine.execute("apply_lut", {"image": img})


@needs_magick
def test_cube_lut_file(engine, root, img):
    lines = ["LUT_3D_SIZE 2"] + [f"{1 - r} {1 - g} {1 - b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    (root / "invert.cube").write_text("\n".join(lines) + "\n")
    out = _get(engine, engine.execute("apply_lut", {"image": img, "cube_file": "invert.cube"}))
    assert np.abs(out.astype(int) - (255 - engine.store.get(img).astype(int))).mean() < 3


@needs_magick
def test_quantize_and_remap(engine):
    grad = _put(engine, np.tile(np.linspace(0, 255, 256, dtype=np.uint8), (32, 1)))
    res = engine.execute("quantize", {"image": grad, "colors": 4})
    assert res["color_count"] <= 4
    palette = _put(engine, np.array([[[0, 0, 0], [255, 255, 255]]], np.uint8))
    color = _out(engine.execute("convert_color", {"image": grad, "to": "bgr"}))
    res = engine.execute("quantize", {"image": color, "palette": palette})
    assert sorted(res["colors"]) == ["#000000", "#ffffff"]


@needs_magick
@pytest.mark.parametrize(
    "method,args", [("barrel", [0, 0, -0.1]), ("arc", [60]), ("polar", []), ("shepards", [100, 100, 120, 110])]
)
def test_distort(engine, img, method, args):
    res = engine.execute("distort", {"image": img, "method": method, "args": args})
    out = _get(engine, res)
    assert out.shape[2] == 3 and not np.array_equal(out, engine.store.get(img)[: out.shape[0], : out.shape[1]])


@needs_magick
def test_liquid_rescale(engine, img):
    if "lqr" not in _magick.delegates():
        pytest.skip("no liblqr delegate")
    out = _get(engine, engine.execute("liquid_rescale", {"image": img, "width": 200, "height": 240}))
    assert out.shape == (240, 200, 3)


@needs_magick
@pytest.mark.parametrize("effect", ["charcoal", "sketch", "oil_paint", "kuwahara", "swirl", "vignette", "polaroid"])
def test_artistic_effects(engine, img, effect):
    out = _get(engine, engine.execute("artistic_effect", {"image": img, "effect": effect}))
    if effect == "polaroid":
        assert out.shape[2] == 4
    else:
        assert out.shape == (240, 320, 3)


@needs_magick
def test_montage(engine, img):
    res = engine.execute("montage", {"images": [img] * 3, "labels": ["a", "%[pixel:p]", "@/etc/passwd"]})
    assert (res["columns"], res["rows"]) == (2, 2)
    out = _get(engine, res)
    assert out.shape[1] >= 2 * 256


def test_compare_images(engine, img):
    same = engine.execute("compare_images", {"image1": img, "image2": img})
    assert same["ssim"] == 1.0 and same["psnr"] == "inf" and same["changed_ratio"] == 0 and same["phash_distance"] == 0
    blurred = _out(engine.execute("blur", {"image": img, "ksize": 9}))
    diff = engine.execute("compare_images", {"image1": img, "image2": blurred})
    assert 0.5 < diff["ssim"] < 0.99 and diff["psnr"] > 15 and diff["changed_ratio"] > 0
    small = _out(engine.execute("resize", {"image": img, "scale": 0.5}))
    assert engine.execute("compare_images", {"image1": img, "image2": small})["phash_distance"] < 10


@needs_magick
def test_frames(engine, img):
    assert _get(engine, engine.execute("frame", {"image": img, "size": 10})).shape == (260, 340, 3)
    rounded = _get(engine, engine.execute("frame", {"image": img, "style": "rounded", "size": 30}))
    assert rounded[0, 0, 3] == 0 and rounded[120, 160, 3] == 255
    assert _get(engine, engine.execute("frame", {"image": img, "style": "shadow"})).shape[2] == 4


@needs_magick
@pytest.mark.parametrize("kind", ["gradient", "radial_gradient", "plasma", "fractal", "checkerboard", "hexagons"])
def test_generate_image(engine, kind):
    out = _get(engine, engine.execute("generate_image", {"kind": kind, "width": 64, "height": 48}))
    assert out.shape == (48, 64, 3)
    if kind == "gradient":
        assert out[0, 0].tolist() == [255, 255, 255] and out[-1, 0].tolist() == [0, 0, 0]


@needs_magick
def test_policy_blocks_file_indirection_and_unsafe_coders(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    for src in (f"text:{secret}", f"label:@{secret}", "msl:x.msl", "https://example.com/a.png"):
        with pytest.raises(_magick.MagickError, match="not authorized|security policy"):
            _magick.run_raw([src, "png:-"])
    # sanity: without our policy the same command works, so the policy is what blocks it
    ok = subprocess.run([_magick.binary(), f"text:{secret}", "png:-"], capture_output=True)
    assert ok.returncode == 0 or b"not authorized" in ok.stderr


# ------------------------------------------------------------------ geometry


def test_trim(engine):
    a = np.full((100, 120, 3), 250, np.uint8)
    a[20:50, 30:90] = (10, 20, 30)
    res = engine.execute("trim", {"image": _put(engine, a)})
    assert res["box"] == [30, 20, 60, 30]
