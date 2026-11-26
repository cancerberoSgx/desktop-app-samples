"""Behaviour tests for the features, detection and segmentation modules and
the inpaint / stitch_images ops."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from my_image_mcp.core.engine import OpError


def _put(engine, arr: np.ndarray) -> str:
    return engine.store.put("image", arr)


def _out(res, i: int = 0) -> str:
    return res["outputs"][i]["handle"]


@pytest.fixture
def texture() -> np.ndarray:
    """Feature-rich, non-repeating test image."""
    rng = np.random.default_rng(1)
    img = cv2.resize(rng.integers(0, 255, (60, 80, 3), dtype=np.uint8), (640, 480), interpolation=cv2.INTER_CUBIC)
    for _ in range(40):
        x, y, r = (int(v) for v in rng.integers(20, 460, 3))
        cv2.circle(img, (x, y), r % 40 + 5, tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    return img


# ------------------------------------------------------------------ features


def test_corners_of_rectangle(engine):
    img = np.zeros((200, 200), np.uint8)
    cv2.rectangle(img, (50, 60), (150, 140), 255, -1)
    res = engine.execute("detect_corners", {"image": _put(engine, img), "max_corners": 4, "annotate": True})
    found = sorted((round(x), round(y)) for x, y in res["corners"])
    assert res["count"] == 4 and len(res["outputs"]) == 1
    for (x, y), (ex, ey) in zip(found, sorted([(50, 60), (50, 140), (150, 60), (150, 140)])):
        assert abs(x - ex) <= 2 and abs(y - ey) <= 2


@pytest.mark.parametrize("detector", ["orb", "sift", "akaze", "brisk"])
def test_keypoints(engine, texture, detector):
    res = engine.execute("detect_keypoints", {"image": _put(engine, texture), "detector": detector, "max_report": 5})
    assert res["count"] > 20 and len(res["keypoints"]) == 5


def test_match_and_align_recover_shift(engine, texture):
    ref = _put(engine, texture)
    shifted = _put(engine, cv2.warpAffine(texture, np.float32([[1, 0, 25], [0, 1, -15]]), (640, 480)))
    res = engine.execute("match_features", {"image1": shifted, "image2": ref, "annotate": True})
    assert res["inliers"] > 30
    h = np.array(res["matrix"])
    assert abs(h[0, 2] + 25) < 1.5 and abs(h[1, 2] - 15) < 1.5
    aligned = engine.execute("align_images", {"image": shifted, "reference": ref})
    a = engine.store.get(_out(aligned)).astype(int)
    # compare the region that exists in both
    assert np.abs(a[40:440, 40:600] - texture[40:440, 40:600].astype(int)).mean() < 5


def test_align_fails_readably(engine):
    flat = _put(engine, np.zeros((100, 100), np.uint8))
    with pytest.raises(OpError, match="not enough matches"):
        engine.execute("align_images", {"image": flat, "reference": flat})


def test_template_finds_all_occurrences(engine, texture):
    tpl = texture[100:140, 200:250].copy()
    img = texture.copy()
    img[300:340, 500:550] = tpl  # second copy
    res = engine.execute(
        "match_template", {"image": _put(engine, img), "template": _put(engine, tpl), "threshold": 0.95}
    )
    assert res["count"] == 2
    assert sorted(tuple(m["box"][:2]) for m in res["matches"]) == [(200, 100), (500, 300)]


# ----------------------------------------------------------------- detection


def test_hough_lines_and_circles(engine, img):
    canvas = np.zeros((300, 300), np.uint8)
    cv2.line(canvas, (20, 50), (280, 50), 255, 2)
    cv2.line(canvas, (150, 80), (150, 280), 255, 2)
    res = engine.execute("hough_lines", {"image": _put(engine, canvas), "annotate": True})
    angles = sorted({round(d["angle"]) % 180 for d in res["lines"]})
    assert 0 in angles and 90 in angles and len(res["outputs"]) == 1

    # make_shapes() has a white circle at (70, 70) r=40
    res = engine.execute("hough_circles", {"image": img, "min_radius": 20, "max_radius": 60})
    c = res["circles"][0]
    assert abs(c["center"][0] - 70) < 4 and abs(c["center"][1] - 70) < 4 and abs(c["radius"] - 40) < 4


def test_qr_round_trip(engine):
    qr = _out(engine.execute("create_marker", {"kind": "qr", "text": "my-image-mcp"}))
    res = engine.execute("detect_codes", {"image": qr, "annotate": True})
    assert [c["text"] for c in res["codes"]] == ["my-image-mcp"]


def test_aruco_round_trip(engine):
    m = _out(engine.execute("create_marker", {"kind": "aruco", "marker_id": 7, "dictionary": "5x5_100"}))
    rotated = _out(engine.execute("rotate", {"image": m, "angle": 30, "expand": True, "border_color": "white"}))
    res = engine.execute("detect_markers", {"image": rotated, "dictionary": "5x5_100"})
    assert [d["id"] for d in res["markers"]] == [7]
    assert abs(abs(res["markers"][0]["rotation"]) - 30) < 3
    with pytest.raises(OpError, match="marker_id must be"):
        engine.execute("create_marker", {"kind": "aruco", "marker_id": 50})


def test_haar_and_people_run_without_false_hits_on_shapes(engine, img):
    for target in ("face", "eye", "full_body"):
        assert engine.execute("detect_haar", {"image": img, "target": target})["count"] == 0
    assert engine.execute("detect_people", {"image": img})["count"] == 0


# -------------------------------------------------------------- segmentation


def test_kmeans_palette(engine, img):
    res = engine.execute("kmeans_colors", {"image": img, "k": 4})
    colors = {p["color"] for p in res["palette"]}
    assert colors == {"#1e1e1e", "#ffffff", "#ff0000", "#00ff00"}
    assert res["palette"][0]["color"] == "#1e1e1e"  # background dominates
    assert abs(sum(p["ratio"] for p in res["palette"]) - 1) < 1e-3
    labels = engine.store.get(_out(res, 1))
    assert labels.dtype == np.int32 and labels[0, 0] == 0


def test_flood_fill_region(engine, img):
    res = engine.execute("flood_fill", {"image": img, "x": 200, "y": 70, "color": "blue"})
    # red rectangle (160,40)-(280,110) inclusive
    assert res["area"] == 121 * 71
    filled = engine.store.get(_out(res))
    assert filled[70, 200].tolist() == [255, 0, 0]
    assert engine.store.get(img)[70, 200].tolist() == [0, 0, 255]  # input untouched


def test_watershed_splits_touching_discs(engine):
    mask = np.zeros((200, 300), np.uint8)
    cv2.circle(mask, (100, 100), 50, 255, -1)
    cv2.circle(mask, (185, 100), 50, 255, -1)
    m = _put(engine, mask)
    assert engine.execute("find_contours", {"image": m})["count"] == 1
    res = engine.execute("watershed", {"image": m})
    assert res["count"] == 2
    assert res["outputs"][1]["kind"] == "contours"


def test_grabcut_extracts_object(engine):
    img = np.full((200, 200, 3), (40, 120, 40), np.uint8)
    cv2.circle(img, (100, 100), 45, (30, 30, 220), -1)
    res = engine.execute("grabcut", {"image": _put(engine, img), "x": 40, "y": 40, "width": 120, "height": 120})
    mask = engine.store.get(_out(res))
    assert mask[100, 100] == 255 and mask[10, 10] == 0 and mask[100, 140] == 255 and mask[100, 150] == 0


def test_superpixels_and_mean_shift(engine, img):
    res = engine.execute("superpixels", {"image": img, "region_size": 40})
    assert 20 < res["count"] < 120 and len(res["outputs"]) == 3
    engine.execute("mean_shift", {"image": img})


# --------------------------------------------------------- filters / geometry


def test_inpaint_removes_scribble(engine, texture):
    smooth = cv2.GaussianBlur(texture, (31, 31), 0)
    damaged = smooth.copy()
    mask = np.zeros(smooth.shape[:2], np.uint8)
    cv2.line(mask, (50, 240), (590, 240), 255, 5)
    damaged[mask > 0] = 255
    res = engine.execute("inpaint", {"image": _put(engine, damaged), "mask": _put(engine, mask)})
    fixed = engine.store.get(_out(res)).astype(int)
    err = np.abs(fixed[mask > 0] - smooth[mask > 0].astype(int)).mean()
    assert err < 10 and res["inpainted_pixels"] == int((mask > 0).sum())


def test_stitch_scans(engine, texture):
    left, right = _put(engine, texture[:, :400].copy()), _put(engine, texture[:, 240:].copy())
    res = engine.execute("stitch_images", {"images": [left, right], "mode": "scans"})
    assert abs(res["width"] - 640) < 20 and abs(res["height"] - 480) < 20
