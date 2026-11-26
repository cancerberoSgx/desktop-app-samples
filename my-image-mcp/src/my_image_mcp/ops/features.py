"""Corners, keypoints, feature matching, image alignment and template matching.

Keypoints and descriptors are not stored as handles: each op detects what it
needs and returns plain JSON (points, matches, matrices), so results can be
fed straight into drawing, geometry and pipeline steps.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..core.registry import op
from ..core.types import Img, Result
from ._util import ensure_color_for, match_to, parse_color, to_bgr, to_uint8, to_uint8_gray

Detector = Literal["orb", "sift", "akaze", "brisk"]


def _detect(image: np.ndarray, detector: str, max_features: int) -> tuple[list, np.ndarray | None, int]:
    """Detect keypoints + descriptors, keeping the ``max_features`` strongest."""
    if detector == "orb":
        det, norm = cv2.ORB_create(nfeatures=max_features), cv2.NORM_HAMMING
    elif detector == "sift":
        det, norm = cv2.SIFT_create(nfeatures=max_features), cv2.NORM_L2
    elif detector == "akaze":
        det, norm = cv2.AKAZE_create(), cv2.NORM_HAMMING
    else:
        det, norm = cv2.BRISK_create(), cv2.NORM_HAMMING
    kps, desc = det.detectAndCompute(to_uint8_gray(image), None)
    kps = list(kps)
    if len(kps) > max_features:
        keep = np.argsort([-k.response for k in kps])[:max_features]
        kps = [kps[i] for i in keep]
        desc = desc[keep] if desc is not None else None
    return kps, desc, norm


def _kp_json(k: cv2.KeyPoint) -> dict:
    return {
        "x": round(k.pt[0], 2),
        "y": round(k.pt[1], 2),
        "size": round(k.size, 2),
        "angle": round(k.angle, 2),
        "response": round(float(k.response), 6),
    }


def _match(image1, image2, detector, max_features, ratio):
    kp1, d1, norm = _detect(image1, detector, max_features)
    kp2, d2, _ = _detect(image2, detector, max_features)
    good = []
    if d1 is not None and d2 is not None and len(kp1) >= 2 and len(kp2) >= 2:
        pairs = cv2.BFMatcher(norm).knnMatch(d1, d2, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < ratio * p[1].distance]
        good.sort(key=lambda m: m.distance)
    return kp1, kp2, good


def _estimate(kp1, kp2, matches, model: str, ransac_threshold: float):
    """Return (matrix, inlier mask) mapping image1 points onto image2."""
    need = 4 if model == "homography" else 3
    if len(matches) < need:
        return None, None
    src = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    if model == "homography":
        matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac_threshold)
    else:
        matrix, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=ransac_threshold)
    return matrix, mask


@op("features")
def detect_corners(
    image: Img,
    max_corners: int = 100,
    quality: float = 0.01,
    min_distance: float = 10.0,
    method: Literal["shi_tomasi", "harris"] = "shi_tomasi",
    subpixel: bool = False,
    annotate: bool = False,
    color: str = "red",
) -> Result:
    """Find strong corners (good features to track). quality is relative to the best corner (0-1).
    Returns corners as [[x, y], ...] (strongest first); annotate=True also returns a copy with them marked."""
    gray = to_uint8_gray(image)
    pts = cv2.goodFeaturesToTrack(
        gray, max_corners, quality, min_distance, useHarrisDetector=method == "harris", blockSize=3
    )
    pts = np.zeros((0, 1, 2), np.float32) if pts is None else pts
    if subpixel and len(pts):
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
        pts = cv2.cornerSubPix(gray, pts.astype(np.float32), (5, 5), (-1, -1), crit)
    corners = [[round(float(x), 2), round(float(y), 2)] for x, y in pts.reshape(-1, 2)]
    outputs = []
    if annotate:
        out = ensure_color_for(image, color)
        c = parse_color(color, 1 if out.ndim == 2 else out.shape[2])
        for x, y in corners:
            cv2.drawMarker(out, (int(round(x)), int(round(y))), c, cv2.MARKER_CROSS, 10, 2, cv2.LINE_AA)
        outputs.append(out)
    return Result(outputs, {"count": len(corners), "corners": corners})


@op("features")
def detect_keypoints(
    image: Img,
    detector: Detector = "orb",
    max_features: int = 500,
    annotate: bool = False,
    max_report: int = 50,
) -> Result:
    """Detect scale/rotation-invariant keypoints. orb: fast, binary; sift: most robust, slower;
    akaze / brisk: good middle ground. Reports the strongest max_report (x, y, size, angle, response);
    annotate=True also returns a copy with rich keypoint circles drawn."""
    kps, _, _ = _detect(image, detector, max_features)
    kps.sort(key=lambda k: -k.response)
    outputs = []
    if annotate:
        flags = cv2.DRAW_MATCHES_FLAGS_DRAW_RICH_KEYPOINTS
        outputs.append(cv2.drawKeypoints(to_bgr(to_uint8(image)), kps, None, (0, 255, 0), flags))
    return Result(
        outputs,
        {"count": len(kps), "keypoints": [_kp_json(k) for k in kps[:max_report]], "truncated": len(kps) > max_report},
    )


@op("features")
def match_features(
    image1: Img,
    image2: Img,
    detector: Detector = "orb",
    max_features: int = 1000,
    ratio: float = 0.75,
    estimate: Literal["homography", "affine", "none"] = "homography",
    ransac_threshold: float = 5.0,
    annotate: bool = False,
    max_report: int = 30,
) -> Result:
    """Match keypoints between two images (Lowe ratio test), then optionally fit a RANSAC homography
    (perspective) or partial affine (rotation + scale + shift) mapping image1 -> image2.
    Use to check whether two images show the same object/scene and how it moved.
    annotate=True also returns a side-by-side image of the (inlier) matches."""
    kp1, kp2, good = _match(image1, image2, detector, max_features, ratio)
    data: dict = {"keypoints1": len(kp1), "keypoints2": len(kp2), "matches": len(good)}
    inlier_mask = None
    if estimate != "none":
        matrix, inlier_mask = _estimate(kp1, kp2, good, estimate, ransac_threshold)
        data["matrix"] = None if matrix is None else np.round(matrix, 6).tolist()
        data["inliers"] = 0 if inlier_mask is None else int(inlier_mask.sum())
        data["inlier_ratio"] = round(data["inliers"] / len(good), 4) if good else 0.0
    shown = good if inlier_mask is None else [m for m, ok in zip(good, inlier_mask.ravel()) if ok]
    data["pairs"] = [
        {
            "pt1": [round(kp1[m.queryIdx].pt[0], 2), round(kp1[m.queryIdx].pt[1], 2)],
            "pt2": [round(kp2[m.trainIdx].pt[0], 2), round(kp2[m.trainIdx].pt[1], 2)],
            "distance": round(float(m.distance), 3),
        }
        for m in shown[:max_report]
    ]
    outputs = []
    if annotate:
        flags = cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS
        vis = cv2.drawMatches(to_bgr(to_uint8(image1)), kp1, to_bgr(to_uint8(image2)), kp2, shown, None, flags=flags)
        outputs.append(vis)
    return Result(outputs, data)


@op("features")
def align_images(
    image: Img,
    reference: Img,
    detector: Detector = "orb",
    max_features: int = 2000,
    ratio: float = 0.75,
    model: Literal["homography", "affine"] = "homography",
    ransac_threshold: float = 5.0,
) -> Result:
    """Register image onto reference: match features, fit a RANSAC homography (or partial affine) and
    warp image into reference's frame (same size). Use before difference/blend on photos of the same
    scene, or to straighten a photographed document against a template."""
    kp1, kp2, good = _match(image, reference, detector, max_features, ratio)
    matrix, mask = _estimate(kp1, kp2, good, model, ransac_threshold)
    if matrix is None:
        raise ValueError(f"not enough matches to align ({len(good)} found); try detector='sift' or a higher ratio")
    h, w = reference.shape[:2]
    if model == "homography":
        out = cv2.warpPerspective(image, matrix, (w, h))
    else:
        out = cv2.warpAffine(image, matrix, (w, h))
    inliers = int(mask.sum()) if mask is not None else 0
    return Result(
        [out],
        {"matrix": np.round(matrix, 6).tolist(), "matches": len(good), "inliers": inliers},
    )


@op("features")
def match_template(
    image: Img,
    template: Img,
    method: Literal["ccoeff_normed", "ccorr_normed", "sqdiff_normed"] = "ccoeff_normed",
    threshold: float = 0.8,
    max_matches: int = 20,
    annotate: bool = False,
    color: str = "lime",
) -> Result:
    """Find where a small template image occurs in image (same scale and rotation only).
    Scores are 0-1 (1 = perfect, for every method). Returns non-overlapping matches above threshold as
    {"box": [x, y, w, h], "score"} (boxes work with draw_boxes) plus the best match even if below threshold."""
    tpl = match_to(template, image, resize=False)
    th, tw = tpl.shape[:2]
    if th > image.shape[0] or tw > image.shape[1]:
        raise ValueError("template is larger than the image")
    methods = {
        "ccoeff_normed": cv2.TM_CCOEFF_NORMED,
        "ccorr_normed": cv2.TM_CCORR_NORMED,
        "sqdiff_normed": cv2.TM_SQDIFF_NORMED,
    }
    res = cv2.matchTemplate(image, tpl, methods[method])
    score = 1.0 - res if method == "sqdiff_normed" else res.copy()
    score = np.nan_to_num(score, nan=0.0)
    _, best_val, _, best_loc = cv2.minMaxLoc(score)
    best = {"box": [int(best_loc[0]), int(best_loc[1]), tw, th], "score": round(float(best_val), 4)}
    matches = []
    while len(matches) < max_matches:
        _, val, _, (x, y) = cv2.minMaxLoc(score)
        if val < threshold:
            break
        matches.append({"box": [int(x), int(y), tw, th], "score": round(float(val), 4)})
        # suppress the neighbourhood so the next peak is a different occurrence
        score[max(0, y - th // 2) : y + th // 2 + 1, max(0, x - tw // 2) : x + tw // 2 + 1] = -1.0
    outputs = []
    if annotate:
        out = ensure_color_for(image, color)
        c = parse_color(color, 1 if out.ndim == 2 else out.shape[2])
        for m in matches:
            x, y, w, h = m["box"]
            cv2.rectangle(out, (x, y), (x + w, y + h), c, 2)
        outputs.append(out)
    return Result(outputs, {"count": len(matches), "matches": matches, "best": best})
