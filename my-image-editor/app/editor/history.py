"""Undo/redo as a stack of snapshots.

A snapshot is (base image, copies of the shapes, label). Shapes are tiny, so
copying all of them per step is simpler and safer than inverse commands. The
base image is never mutated in place (crop/resize/flatten produce a new
Pillow image), so a snapshot only holds a *reference* to it: steps that
don't touch pixels share the same image object and cost no image memory.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image

from app.editor.shapes import Shape

DEFAULT_LIMIT = 100


@dataclass(frozen=True)
class Snapshot:
    image: Image.Image
    shapes: Tuple[Shape, ...]
    label: str      # the action this snapshot undoes, e.g. "Move"
    state_id: int   # identifies the document state (for "modified since save")


class History:
    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self.limit = limit
        self._undo: List[Snapshot] = []
        self._redo: List[Snapshot] = []

    def push(self, snapshot: Snapshot) -> None:
        """Record the state *before* a change; a new change forgets redo."""
        self._undo.append(snapshot)
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()

    def pop_last(self) -> Optional[Snapshot]:
        """Drop the most recent push (a change that turned out to be a no-op)."""
        return self._undo.pop() if self._undo else None

    def undo(self, current: Snapshot) -> Optional[Snapshot]:
        if not self._undo:
            return None
        previous = self._undo.pop()
        # Redo re-applies the action named by the snapshot we go back to.
        self._redo.append(Snapshot(current.image, current.shapes, previous.label, current.state_id))
        return previous

    def redo(self, current: Snapshot) -> Optional[Snapshot]:
        if not self._redo:
            return None
        following = self._redo.pop()
        self._undo.append(Snapshot(current.image, current.shapes, following.label, current.state_id))
        return following

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> Optional[str]:
        return self._undo[-1].label if self._undo else None

    def redo_label(self) -> Optional[str]:
        return self._redo[-1].label if self._redo else None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
