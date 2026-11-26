"""Value types shared by ops, the store and the MCP layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

import numpy as np


class Contours(list):
    """A list of OpenCV contours (each an ``(N, 1, 2)`` int32 array)."""


@dataclass
class Result:
    """What every op returns.

    ``outputs`` are values that get stored and handed back as handles
    (images, contour sets); ``data`` is JSON-serialisable information
    (measurements, statistics) returned to the caller as-is.
    """

    outputs: list[Any] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def image(self) -> Any:
        return self.outputs[0]


class _PathRole:
    def __init__(self, write: bool):
        self.write = write

    def __repr__(self) -> str:
        return f"PathRole(write={self.write})"


READ = _PathRole(write=False)
WRITE = _PathRole(write=True)

# Parameter annotations with special meaning for the engine:
Img = np.ndarray  # an image handle (e.g. "img_3") in the MCP schema
InPath = Annotated[str, READ]  # a file path that is read (sandboxed)
OutPath = Annotated[str, WRITE]  # a file path that is written (sandboxed)

HANDLE_KINDS: dict[type, str] = {np.ndarray: "image", Contours: "contours"}
HANDLE_PREFIX = {
    "image": "img",
    "contours": "cnt",
    "array": "arr",
    "sequence": "seq",
    "object": "obj",
}


def kind_of(value: Any) -> str:
    if isinstance(value, Contours):
        return "contours"
    if isinstance(value, np.ndarray):
        return "image"
    raise TypeError(f"Cannot store value of type {type(value).__name__} as an op output")
