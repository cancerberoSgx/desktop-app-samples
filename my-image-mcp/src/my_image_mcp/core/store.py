"""In-memory handle store with LRU eviction by total byte size."""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np

from .types import HANDLE_PREFIX, Contours


@dataclass
class Item:
    handle: str
    kind: str
    value: Any
    nbytes: int


def _nbytes(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, (list, tuple)):
        return sum(_nbytes(v) for v in value) + 64
    return 256


def describe(kind: str, value: Any) -> dict[str, Any]:
    """Short, JSON-friendly description of a stored value."""
    if kind == "image":
        h, w = value.shape[:2]
        ch = 1 if value.ndim == 2 else value.shape[2]
        return {"width": int(w), "height": int(h), "channels": int(ch), "dtype": str(value.dtype)}
    if kind == "contours":
        return {"count": len(value)}
    if kind == "array":
        return {"shape": list(value.shape), "dtype": str(value.dtype)}
    if kind == "sequence":
        return {"length": len(value), "item_type": type(value[0]).__name__ if len(value) else None}
    return {"type": type(value).__name__}


class Store:
    def __init__(self, max_bytes: int = 2048 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._items: OrderedDict[str, Item] = OrderedDict()
        self._counter = itertools.count(1)
        self._lock = threading.RLock()

    @property
    def total_bytes(self) -> int:
        return sum(i.nbytes for i in self._items.values())

    def put(self, kind: str, value: Any) -> str:
        if kind == "contours" and not isinstance(value, Contours):
            value = Contours(value)
        with self._lock:
            handle = f"{HANDLE_PREFIX[kind]}_{next(self._counter)}"
            self._items[handle] = Item(handle, kind, value, _nbytes(value))
            self._evict(keep=handle)
            return handle

    def _evict(self, keep: str) -> None:
        while self.total_bytes > self.max_bytes and len(self._items) > 1:
            oldest = next(iter(self._items))
            if oldest == keep:
                break
            del self._items[oldest]

    def get(self, handle: str, kind: str | None = None) -> Any:
        with self._lock:
            item = self._items.get(handle)
            if item is None:
                raise KeyError(
                    f"Unknown handle '{handle}'. It may never have existed or been evicted; "
                    "use list_images to see what is available."
                )
            if kind is not None and item.kind != kind:
                raise TypeError(f"Handle '{handle}' is a {item.kind}, expected a {kind}.")
            self._items.move_to_end(handle)
            return item.value

    def kind(self, handle: str) -> str | None:
        item = self._items.get(handle)
        return item.kind if item else None

    def __contains__(self, handle: object) -> bool:
        return handle in self._items

    def delete(self, handle: str) -> bool:
        with self._lock:
            return self._items.pop(handle, None) is not None

    def items(self) -> list[Item]:
        return list(self._items.values())
