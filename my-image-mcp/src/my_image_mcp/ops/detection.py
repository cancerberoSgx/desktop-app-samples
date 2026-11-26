"""Classic (non-DNN) detectors: Hough lines/circles, Haar cascades, HOG people,
QR codes / barcodes and ArUco / AprilTag markers.

Detections come back as JSON. Boxes are always [x, y, width, height] so they
can be passed to draw_boxes / crop; annotate=True also returns a marked-up
copy of the input.
"""

from __future__ import annotations

import functools
import os
from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import channels, ensure_color_for, parse_color, to_bgr, to_uint8, to_uint8_gray


def _canvas(image: np.ndarray, color: str) -> tuple[np.ndarray, tuple]:
    out = ensure_color_for(to_uint8(image), color)
    return out, parse_color(color, channels(out))


def _boxes_json(boxes) -> list[list[int]]:
    return [[int(v) for v in b] for b in boxes]


def _draw_boxes(out: np.ndarray, boxes: list[list[int]], c: tuple, labels: list[str] | None = None) -> None:
    for i, (x, y, w, h) in enumerate(boxes):
        cv2.rectangle(out, (x, y), (x + w, y + h), c, 2, cv2.LINE_AA)
        if labels:
            cv2.putText(out, labels[i], (x, max(y - 5, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)


def _edges(image: np.ndarray) -> np.ndarray:
    """Use image as-is if it is already binary, else auto-threshold Canny."""
    g = to_uint8_gray(image)
    if not np.any((g != 0) & (g != 255)):
        return g
    med = float(np.median(g))
    return cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), int(max(0, 0.67 * med)), int(min(255, 1.33 * med)))


@op("detection")
def hough_lines(
    image: Img,
    threshold: int = 50,
    min_length: float = 30.0,
    max_gap: float = 10.0,
    angle_resolution: float = 1.0,
    annotate: bool = False,
    color: str = "red",
    max_report: int = 100,
) -> Result:
    """Detect straight line segments (probabilistic Hough). Works on an edge map; a non-binary image
    gets automatic Canny first. threshold = minimum votes. Returns segments [x1, y1, x2, y2] with
    length and angle in degrees (0 = horizontal, 90 = vertical), longest first."""
    found = cv2.HoughLinesP(
        _edges(image), 1, np.deg2rad(angle_resolution), threshold, minLineLength=min_length, maxLineGap=max_gap
    )
    segs = [] if found is None else found.reshape(-1, 4).tolist()
    items = []
    for x1, y1, x2, y2 in segs:
        length = float(np.hypot(x2 - x1, y2 - y1))
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        items.append({"line": [x1, y1, x2, y2], "length": round(length, 2), "angle": round(angle, 2)})
    items.sort(key=lambda d: -d["length"])
    outputs = []
    if annotate:
        out, c = _canvas(image, color)
        for d in items:
            x1, y1, x2, y2 = d["line"]
            cv2.line(out, (x1, y1), (x2, y2), c, 2, cv2.LINE_AA)
        outputs.append(out)
    return Result(outputs, {"count": len(items), "lines": items[:max_report], "truncated": len(items) > max_report})


@op("detection")
def hough_circles(
    image: Img,
    min_radius: int = 0,
    max_radius: int = 0,
    min_distance: float = 20.0,
    edge_threshold: float = 100.0,
    accumulator_threshold: float = 30.0,
    dp: float = 1.2,
    annotate: bool = False,
    color: str = "red",
    max_report: int = 100,
) -> Result:
    """Detect circles (Hough gradient) in a grayscale/color image (smoothed internally).
    max_radius=0 means unbounded. Lower accumulator_threshold finds more (and more false) circles;
    min_distance is between centers. Returns {"center": [x, y], "radius", "box"} strongest first."""
    g = cv2.medianBlur(to_uint8_gray(image), 5)
    found = cv2.HoughCircles(
        g,
        cv2.HOUGH_GRADIENT,
        dp,
        min_distance,
        param1=edge_threshold,
        param2=accumulator_threshold,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    circles = []
    for x, y, r in [] if found is None else found[0]:
        circles.append(
            {
                "center": [round(float(x), 1), round(float(y), 1)],
                "radius": round(float(r), 1),
                "box": [int(x - r), int(y - r), int(2 * r), int(2 * r)],
            }
        )
    outputs = []
    if annotate:
        out, c = _canvas(image, color)
        for d in circles:
            (x, y), r = d["center"], d["radius"]
            cv2.circle(out, (int(x), int(y)), int(r), c, 2, cv2.LINE_AA)
            cv2.drawMarker(out, (int(x), int(y)), c, cv2.MARKER_CROSS, 8, 1)
        outputs.append(out)
    return Result(
        outputs, {"count": len(circles), "circles": circles[:max_report], "truncated": len(circles) > max_report}
    )


_CASCADES = {
    "face": "haarcascade_frontalface_default.xml",
    "face_alt": "haarcascade_frontalface_alt2.xml",
    "profile_face": "haarcascade_profileface.xml",
    "eye": "haarcascade_eye.xml",
    "eye_glasses": "haarcascade_eye_tree_eyeglasses.xml",
    "smile": "haarcascade_smile.xml",
    "full_body": "haarcascade_fullbody.xml",
    "upper_body": "haarcascade_upperbody.xml",
    "lower_body": "haarcascade_lowerbody.xml",
    "cat_face": "haarcascade_frontalcatface_extended.xml",
    "license_plate": "haarcascade_russian_plate_number.xml",
}
HaarTarget = Literal[
    "face",
    "face_alt",
    "profile_face",
    "eye",
    "eye_glasses",
    "smile",
    "full_body",
    "upper_body",
    "lower_body",
    "cat_face",
    "license_plate",
]


@functools.cache
def _cascade(target: str) -> cv2.CascadeClassifier:
    clf = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, _CASCADES[target]))
    if clf.empty():
        raise ValueError(f"could not load the Haar cascade for '{target}'")
    return clf


@op("detection")
def detect_haar(
    image: Img,
    target: HaarTarget = "face",
    scale_factor: float = 1.1,
    min_neighbors: int = 5,
    min_size: int = 30,
    annotate: bool = False,
    color: str = "lime",
) -> Result:
    """Detect faces, eyes, smiles, bodies, cat faces or license plates with OpenCV's bundled Haar
    cascades (fast, classic, works best on upright frontal views; expect some misses/false hits).
    Raise min_neighbors to reduce false positives. Returns boxes [x, y, w, h], largest first.
    Tip: for eyes/smiles, crop to a detected face first."""
    gray = cv2.equalizeHist(to_uint8_gray(image))
    found = _cascade(target).detectMultiScale(
        gray, scaleFactor=scale_factor, minNeighbors=min_neighbors, minSize=(min_size, min_size)
    )
    boxes = sorted(_boxes_json(found), key=lambda b: -b[2] * b[3])
    outputs = []
    if annotate:
        out, c = _canvas(image, color)
        _draw_boxes(out, boxes, c)
        outputs.append(out)
    return Result(outputs, {"target": target, "count": len(boxes), "boxes": boxes})


@op("detection")
def detect_people(
    image: Img,
    hit_threshold: float = 0.0,
    scale: float = 1.05,
    min_score: float = 0.3,
    annotate: bool = False,
    color: str = "lime",
) -> Result:
    """Detect standing/walking people (HOG + linear SVM, the classic pedestrian detector). People should
    be roughly upright and at least ~100 px tall (resize small images up first). Overlapping hits are
    merged. Returns boxes [x, y, w, h] with scores, best first."""
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    rects, weights = hog.detectMultiScale(
        to_bgr(to_uint8(image)), hitThreshold=hit_threshold, winStride=(8, 8), padding=(8, 8), scale=scale
    )
    rects = [list(map(int, r)) for r in rects]
    scores = [float(w) for w in np.asarray(weights).ravel()]
    keep = cv2.dnn.NMSBoxes(rects, scores, min_score, 0.45) if rects else []
    dets = sorted(
        ({"box": rects[i], "score": round(scores[i], 3)} for i in np.asarray(keep).ravel()),
        key=lambda d: -d["score"],
    )
    outputs = []
    if annotate:
        out, c = _canvas(image, color)
        _draw_boxes(out, [d["box"] for d in dets], c, [f"{d['score']:.2f}" for d in dets])
        outputs.append(out)
    return Result(outputs, {"count": len(dets), "people": dets, "boxes": [d["box"] for d in dets]})


def _quad_json(pts: np.ndarray) -> dict:
    pts = np.asarray(pts, np.float32).reshape(-1, 2)
    x, y, w, h = cv2.boundingRect(pts)
    return {
        "corners": [[round(float(a), 1), round(float(b), 1)] for a, b in pts],
        "center": [round(float(pts[:, 0].mean()), 1), round(float(pts[:, 1].mean()), 1)],
        "box": [int(x), int(y), int(w), int(h)],
    }


@op("detection")
def detect_codes(
    image: Img,
    kind: Literal["qr", "barcode", "all"] = "all",
    annotate: bool = False,
    color: str = "magenta",
) -> Result:
    """Find and decode QR codes and 1-D barcodes (EAN-8/13, UPC-A/E, Code 39/93/128, ...).
    Returns each code's decoded text (empty if found but unreadable), type, corner points and box.
    For tiny or blurry codes, crop/resize up and sharpen first."""
    img = to_uint8(image)
    codes = []
    if kind in ("qr", "all"):
        ok, texts, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(img)
        if ok and points is not None:
            for text, pts in zip(texts, points):
                codes.append({"type": "QR", "text": text, **_quad_json(pts)})
    if kind in ("barcode", "all"):
        ok, texts, types, points = cv2.barcode.BarcodeDetector().detectAndDecodeWithType(img)
        if ok and points is not None:
            for text, typ, pts in zip(texts, types, points):
                codes.append({"type": typ or "barcode", "text": text, **_quad_json(pts)})
    outputs = []
    if annotate:
        out, c = _canvas(image, color)
        for d in codes:
            cv2.polylines(out, [np.array(d["corners"], np.int32)], True, c, 2, cv2.LINE_AA)
            x, y = d["box"][:2]
            cv2.putText(out, d["text"][:30], (x, max(y - 5, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
        outputs.append(out)
    return Result(outputs, {"count": len(codes), "codes": codes})


_MARKER_DICTS = {
    "4x4_50": cv2.aruco.DICT_4X4_50,
    "4x4_1000": cv2.aruco.DICT_4X4_1000,
    "5x5_100": cv2.aruco.DICT_5X5_100,
    "5x5_1000": cv2.aruco.DICT_5X5_1000,
    "6x6_250": cv2.aruco.DICT_6X6_250,
    "6x6_1000": cv2.aruco.DICT_6X6_1000,
    "7x7_1000": cv2.aruco.DICT_7X7_1000,
    "aruco_original": cv2.aruco.DICT_ARUCO_ORIGINAL,
    "apriltag_16h5": cv2.aruco.DICT_APRILTAG_16h5,
    "apriltag_25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "apriltag_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}
MarkerDict = Literal[
    "4x4_50",
    "4x4_1000",
    "5x5_100",
    "5x5_1000",
    "6x6_250",
    "6x6_1000",
    "7x7_1000",
    "aruco_original",
    "apriltag_16h5",
    "apriltag_25h9",
    "apriltag_36h11",
]


@op("detection")
def detect_markers(
    image: Img,
    dictionary: MarkerDict = "4x4_50",
    annotate: bool = False,
) -> Result:
    """Detect ArUco / AprilTag fiducial markers of a given dictionary. Returns each marker's id,
    4 corners (clockwise from the marker's top-left), center, box and in-image rotation in degrees.
    Corners can be passed to warp_perspective to rectify the marked plane."""
    d = cv2.aruco.getPredefinedDictionary(_MARKER_DICTS[dictionary])
    corners, ids, _ = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()).detectMarkers(to_uint8_gray(image))
    markers = []
    for c, i in zip(corners, [] if ids is None else ids.ravel()):
        pts = c.reshape(4, 2)
        top = pts[1] - pts[0]
        rotation = float(np.degrees(np.arctan2(top[1], top[0])))
        markers.append({"id": int(i), **_quad_json(pts), "rotation": round(rotation, 1)})
    markers.sort(key=lambda m: m["id"])
    outputs = []
    if annotate:
        out = to_bgr(to_uint8(image)).copy()
        if ids is not None:
            cv2.aruco.drawDetectedMarkers(out, corners, ids)
        outputs.append(out)
    return Result(outputs, {"dictionary": dictionary, "count": len(markers), "markers": markers})


@op("detection")
def create_marker(
    kind: Literal["qr", "aruco"] = "qr",
    text: str = "",
    marker_id: int = 0,
    dictionary: MarkerDict = "4x4_50",
    size: int = 300,
    margin: int = 20,
) -> Result:
    """Generate a QR code (encoding text) or an ArUco/AprilTag marker (marker_id from dictionary) as a
    grayscale image of about size x size px plus a white margin. Useful for test images, calibration
    targets or overlays (see overlay)."""
    if kind == "qr":
        if not text:
            raise ValueError("text is required for a QR code")
        q = cv2.QRCodeEncoder.create().encode(text)
        img = cv2.resize(q, (size, size), interpolation=cv2.INTER_NEAREST)
    else:
        d = cv2.aruco.getPredefinedDictionary(_MARKER_DICTS[dictionary])
        if not 0 <= marker_id < d.bytesList.shape[0]:
            raise ValueError(f"marker_id must be 0..{d.bytesList.shape[0] - 1} for dictionary {dictionary}")
        img = cv2.aruco.generateImageMarker(d, marker_id, size)
    img = cv2.copyMakeBorder(img, margin, margin, margin, margin, cv2.BORDER_CONSTANT, value=255)
    return Result([img])
