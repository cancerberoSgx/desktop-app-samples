from __future__ import annotations

import inspect

import numpy as np
import pytest

from my_image_mcp import ops
from my_image_mcp.config import PathNotAllowed
from my_image_mcp.core.engine import OpError
from my_image_mcp.core.registry import REGISTRY

# Values for required (non-handle) parameters, so every op can be smoke-tested.
REQUIRED = {
    "path": "shapes.png",
    "width": 50,
    "height": 40,
    "x": 10,
    "y": 10,
    "x1": 0,
    "y1": 0,
    "x2": 50,
    "y2": 50,
    "cx": 60,
    "cy": 60,
    "radius": 20,
    "axis_x": 30,
    "axis_y": 15,
    "to": "hsv",
    "lower": [0, 0, 200],
    "upper": [255, 255, 255],
    "kernel": [[0, -1, 0], [-1, 5, -1], [0, -1, 0]],
    "angle": 30,
    "matrix": [[1, 0, 10], [0, 1, 5]],
    "src_points": [[10, 10], [300, 20], [290, 230], [5, 220]],
    "points": [[10, 10], [100, 30], [50, 90]],
    "text": "hello",
    "boxes": [[10, 10, 50, 40]],
    "operation": "and",
}


def _args_for(engine, spec, img, contours):
    args = {}
    for p in spec.params:
        if p.default is not inspect.Parameter.empty:
            continue
        if p.handle_kind == "image":
            if p.handle_list:
                gray = engine.execute("convert_color", {"image": img, "to": "gray"})["outputs"][0]["handle"]
                args[p.name] = [gray, gray, gray]
            else:
                args[p.name] = img
        elif p.handle_kind == "contours":
            args[p.name] = contours
        elif spec.name == "save_image" and p.name == "path":
            args[p.name] = "out/saved.png"
        else:
            args[p.name] = REQUIRED[p.name]
    if spec.name in ("blend", "bitwise", "difference"):
        args.setdefault("image2", img)
    return args


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_op_runs(engine, img, name):
    cnt = engine.execute(
        "find_contours", {"image": engine.execute("threshold", {"image": img, "value": 100})["outputs"][0]["handle"]}
    )["outputs"][0]["handle"]
    spec = REGISTRY[name]
    if name == "resize":
        res = engine.execute(name, {"image": img, "scale": 0.5})
    elif name == "create_marker":
        res = engine.execute(name, {"text": "hello"})
    elif name == "stitch_images":
        res = engine.execute(name, {"images": [img, img]})
    else:
        res = engine.execute(name, _args_for(engine, spec, img, cnt))
    assert isinstance(res, dict)


def test_handles_are_immutable(engine, img):
    before = engine.store.get(img).copy()
    engine.execute("draw_rectangle", {"image": img, "x": 0, "y": 0, "width": 50, "height": 50, "thickness": -1})
    engine.execute("cv_call", {"function": "rectangle", "args": [img, [0, 0], [50, 50], [255, 0, 0], -1]})
    assert np.array_equal(before, engine.store.get(img))


def test_threshold_and_contours_measure_shapes(engine, img):
    mask = engine.execute("threshold", {"image": img, "value": 60})["outputs"][0]["handle"]
    res = engine.execute("find_contours", {"image": mask, "min_area": 100})
    assert res["count"] == 3
    shapes = engine.execute("approximate_contours", {"contours": res["outputs"][0]["handle"]})["shapes"]
    names = sorted(s["shape"] for s in shapes)
    assert names == ["circle", "quadrilateral", "triangle"]


def test_hsv_in_range_selects_green(engine, img):
    res = engine.execute("in_range", {"image": img, "lower": [50, 100, 100], "upper": [70, 255, 255], "space": "hsv"})
    # green triangle: base 80px, height 70px -> ~2800 px of 320x240
    assert 0.03 < res["selected_ratio"] < 0.045


def test_color_parsing_is_rgb(engine):
    res = engine.execute("create_image", {"width": 4, "height": 4, "color": "#ff0000"})
    px = engine.store.get(res["outputs"][0]["handle"])[0, 0]
    assert px.tolist() == [0, 0, 255]  # stored BGR


def test_path_sandbox(engine, img):
    with pytest.raises(PathNotAllowed):
        engine.execute("load_image", {"path": "/etc/passwd"})
    with pytest.raises(PathNotAllowed):
        engine.execute("save_image", {"image": img, "path": "../escape.png"})


def test_errors_are_readable(engine, img):
    with pytest.raises(OpError, match="Unknown handle 'img_999'"):
        engine.execute("blur", {"image": "img_999"})
    with pytest.raises(OpError, match="unknown argument"):
        engine.execute("blur", {"image": img, "radius": 3})


def test_ops_usable_as_library(root):
    img = ops.load_image(str(root / "shapes.png")).image
    edges = ops.canny(ops.blur(img, ksize=5).image, auto=True).image
    assert edges.shape == img.shape[:2] and edges.dtype == np.uint8
