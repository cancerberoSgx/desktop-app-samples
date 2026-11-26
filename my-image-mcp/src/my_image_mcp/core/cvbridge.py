"""Generic bridge to (almost) any ``cv2`` function.

The curated ops cover the common cases; this module lets a model reach the
rest of OpenCV through a single ``cv_call`` tool.

Argument encoding (JSON -> Python):

* a string equal to a stored handle (``"img_3"``, ``"cnt_2"`` ...)  -> the stored value
* ``"cv2.THRESH_BINARY"`` / ``"cv2.THRESH_BINARY | cv2.THRESH_OTSU"`` -> int constant
* ``{"$array": [[...]], "dtype": "float32"}``                     -> numpy array
* ``{"$tuple": [...]}``                                           -> tuple
* anything else is passed through as-is (lists, numbers, strings, null)

Return encoding (Python -> JSON): small arrays/scalars are inlined, image-like
arrays become image handles, other large arrays / long lists of OpenCV objects
/ OpenCV class instances are stored and returned as handles. KeyPoint, DMatch
and RotatedRect-like values are converted to dicts.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

# Functions that touch files, devices or GUI. Files go through the sandboxed
# load/save tools; there is no display in a headless server.
_DENY_EXACT = {
    "imshow",
    "waitKey",
    "waitKeyEx",
    "pollKey",
    "namedWindow",
    "destroyWindow",
    "destroyAllWindows",
    "moveWindow",
    "resizeWindow",
    "setWindowProperty",
    "setWindowTitle",
    "getWindowProperty",
    "getWindowImageRect",
    "setMouseCallback",
    "createTrackbar",
    "getTrackbarPos",
    "setTrackbarPos",
    "setTrackbarMax",
    "setTrackbarMin",
    "selectROI",
    "selectROIs",
    "startWindowThread",
    "displayOverlay",
    "displayStatusBar",
    "addText",
    "fontQt",
    "createButton",
    "imcount",
    "haveImageReader",
    "haveImageWriter",
    "VideoCapture",
    "VideoWriter",
    "VideoWriter_fourcc",
    "FileStorage",
    "CascadeClassifier",
    "setNumThreads",
    "setUseOptimized",
    "redirectError",
    "setLogLevel",
    "bootstrap",
}
_DENY_PREFIX = ("imread", "imwrite", "read", "write", "save", "load", "open", "dump")
_DENY_MODULES = {"utils", "samples", "videoio_registry", "Error", "cuda"}

# Optional lowercase submodules (ximgproc., aruco., ml.), then a name starting
# uppercase (THRESH_BINARY, Stitcher_PANORAMA), optionally Class.CONSTANT.
CONST_RE = re.compile(r"^\s*(?:cv2?\.)?((?:[a-z][a-z0-9_]*\.)*[A-Z][A-Za-z0-9_]*(?:\.[A-Z][A-Za-z0-9_]*)?)\s*$")


def _denied(name: str) -> bool:
    return name in _DENY_EXACT or name.lower().startswith(_DENY_PREFIX)


def resolve_function(path: str, lookup: Callable[[str], Any] | None = None) -> tuple[Callable, str]:
    """Resolve ``"GaussianBlur"``, ``"cv2.ximgproc.thinning"`` or
    ``"obj_3.apply"`` (a method on a stored OpenCV object) to a callable."""
    path = path.strip()
    if path.startswith("cv2."):
        path = path[4:]
    elif path.startswith("cv."):
        path = path[3:]
    parts = path.split(".")
    if any(not p or p.startswith("_") for p in parts):
        raise ValueError(f"Invalid function name '{path}'.")
    if any(_denied(p) for p in parts):
        raise PermissionError(
            f"'{path}' is not available via cv_call (GUI/file/device access). Use load_image / save_image for files."
        )

    if lookup is not None and re.match(r"^obj_\d+$", parts[0]):
        obj = lookup(parts[0])
        if len(parts) != 2:
            raise ValueError("Use '<object handle>.<method>' to call a method on a stored object.")
        fn = getattr(obj, parts[1], None)
        if fn is None or not callable(fn):
            raise AttributeError(f"{type(obj).__name__} has no method '{parts[1]}'.")
        return fn, path

    if parts[0] in _DENY_MODULES:
        raise PermissionError(f"Module cv2.{parts[0]} is not available via cv_call.")
    obj: Any = cv2
    for part in parts:
        obj = getattr(obj, part, None)
        if obj is None:
            raise AttributeError(f"cv2.{path} does not exist in OpenCV {cv2.__version__}.")
        mod_name = getattr(obj, "__name__", "") if isinstance(obj, type(cv2)) else None
        if mod_name is not None and not mod_name.startswith("cv2"):
            raise PermissionError(f"cv2.{path} is not an OpenCV function.")
    if not callable(obj) or isinstance(obj, type(cv2)):
        raise TypeError(f"cv2.{path} is not callable.")
    owner = getattr(obj, "__module__", None) or "cv2"
    if not str(owner).startswith("cv2"):
        raise PermissionError(f"cv2.{path} is not an OpenCV function.")
    return obj, path


def resolve_constant(expr: str) -> int | None:
    """``"cv2.A | cv2.B"`` -> int, or None if ``expr`` isn't a constant expression."""
    total = 0
    for term in re.split(r"[|+]", expr):
        m = CONST_RE.match(term)
        if not m:
            return None
        obj: Any = cv2
        for part in m.group(1).split("."):
            obj = getattr(obj, part, None)
        if not isinstance(obj, int):
            return None
        total |= obj
    return total


def decode_arg(value: Any, lookup: Callable[[str], Any], is_handle: Callable[[str], bool]) -> Any:
    if isinstance(value, str):
        if is_handle(value):
            return lookup(value)
        if value.startswith(("cv2.", "cv.")) or "|" in value:
            const = resolve_constant(value)
            if const is not None:
                return const
        return value
    if isinstance(value, dict):
        if "$array" in value:
            return np.array(value["$array"], dtype=value.get("dtype") or None)
        if "$tuple" in value:
            return tuple(decode_arg(v, lookup, is_handle) for v in value["$tuple"])
        return {k: decode_arg(v, lookup, is_handle) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_arg(v, lookup, is_handle) for v in value]
    return value


# ---------------------------------------------------------------- outputs

_IMAGE_DTYPES = {np.uint8, np.uint16, np.int16, np.int32, np.float32, np.float64, np.int8}
INLINE_ARRAY_MAX = 64
INLINE_SEQ_MAX = 32


def _looks_like_image(a: np.ndarray) -> bool:
    if a.dtype.type not in _IMAGE_DTYPES:
        return False
    if a.ndim == 2:
        return a.shape[0] >= 8 and a.shape[1] >= 8
    if a.ndim == 3:
        return a.shape[2] in (1, 3, 4) and a.shape[0] >= 8 and a.shape[1] >= 8
    return False


def _cv_object_to_dict(v: Any) -> dict | None:
    if isinstance(v, cv2.KeyPoint):
        return {
            "pt": [float(v.pt[0]), float(v.pt[1])],
            "size": float(v.size),
            "angle": float(v.angle),
            "response": float(v.response),
            "octave": int(v.octave),
            "class_id": int(v.class_id),
        }
    if isinstance(v, cv2.DMatch):
        return {"queryIdx": v.queryIdx, "trainIdx": v.trainIdx, "imgIdx": v.imgIdx, "distance": float(v.distance)}
    return None


def encode_result(value: Any, store_fn: Callable[[str, Any, tuple], str], path: tuple = ()) -> Any:
    """Convert a cv2 return value to JSON; ``store_fn(kind, value, path)`` stores
    non-inlinable values and returns their handle."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        if value.size <= INLINE_ARRAY_MAX and not _looks_like_image(value):
            return value.tolist()
        kind = "image" if _looks_like_image(value) else "array"
        return {
            "handle": store_fn(kind, value, path),
            "kind": kind,
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }
    as_dict = _cv_object_to_dict(value)
    if as_dict is not None:
        return as_dict
    if isinstance(value, (list, tuple)):
        complex_items = [
            v
            for v in value
            if isinstance(v, np.ndarray) or _cv_object_to_dict(v) is not None or type(v).__module__.startswith("cv2")
        ]
        if len(value) > INLINE_SEQ_MAX and complex_items:
            handle = store_fn("sequence", tuple(value), path)
            sample = [encode_result(v, lambda *a: "(stored)", path + (i,)) for i, v in enumerate(value[:3])]
            return {"handle": handle, "kind": "sequence", "length": len(value), "sample": sample}
        return [encode_result(v, store_fn, path + (i,)) for i, v in enumerate(value)]
    if isinstance(value, dict):
        return {str(k): encode_result(v, store_fn, path + (k,)) for k, v in value.items()}
    # OpenCV class instance (CLAHE, SIFT, BackgroundSubtractor, ...)
    return {"handle": store_fn("object", value, path), "kind": "object", "type": type(value).__name__}


# ---------------------------------------------------------------- discovery


def search(query: str, limit: int = 50) -> list[dict[str, str]]:
    q = query.lower()
    out: list[dict[str, str]] = []
    modules: list[tuple[str, Any]] = [("", cv2)]
    for name in dir(cv2):
        sub = getattr(cv2, name, None)
        if (
            isinstance(sub, type(cv2))
            and name not in _DENY_MODULES
            and not name.startswith("_")
            and getattr(sub, "__name__", "").startswith("cv2")
        ):
            modules.append((name + ".", sub))
    for prefix, mod in modules:
        for name in dir(mod):
            if name.startswith("_") or _denied(name):
                continue
            full = prefix + name
            if q and q not in full.lower():
                continue
            obj = getattr(mod, name, None)
            if not callable(obj) or isinstance(obj, type(cv2)):
                continue
            doc = (getattr(obj, "__doc__", "") or "").strip().splitlines()
            out.append({"name": full, "signature": doc[0] if doc else ""})
            if len(out) >= limit:
                return out
    return out


def doc(function: str) -> str:
    fn, path = resolve_function(function)
    return f"cv2.{path}\n\n{(fn.__doc__ or '(no documentation)').strip()}"
