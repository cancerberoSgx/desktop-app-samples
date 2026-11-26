"""One open image: the base pixels, the editable shapes on top, undo/redo and
"modified since saved".

Every change goes through a method here that calls `checkpoint(label)`
*before* mutating, so it can be undone. Tools that change a shape over a
whole mouse drag call `checkpoint` once at the start and `cancel_checkpoint`
if the drag turned out to change nothing.

Saving renders the shapes into a copy of the image; the document keeps its
shapes, so they stay editable after a save (and saving again re-renders
from the untouched base image, instead of drawing over already-drawn
pixels). "Image > Flatten" bakes them into the base explicitly.
"""
import itertools
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image

from app.editor import imageio
from app.editor.history import History, Snapshot
from app.editor.render import composite
from app.editor.shapes import Shape, new_id

RESAMPLE_FILTERS = (
    ("Lanczos (best)", Image.Resampling.LANCZOS),
    ("Bicubic", Image.Resampling.BICUBIC),
    ("Bilinear", Image.Resampling.BILINEAR),
    ("Nearest (pixel art)", Image.Resampling.NEAREST),
)
MAX_DIMENSION = 30_000


class Document:
    def __init__(self, image: Image.Image, path: Optional[Path] = None,
                 source_format: Optional[str] = None, icc_profile: Optional[bytes] = None) -> None:
        self.image = image if image.mode == "RGBA" else image.convert("RGBA")
        self.shapes: List[Shape] = []
        self.path = Path(path) if path else None
        self.source_format = source_format
        self.icc_profile = icc_profile
        self.history = History()
        self._states = itertools.count(1)
        self.state_id = next(self._states)
        self.saved_state_id = self.state_id
        self._coalesce: Optional[Tuple[str, str]] = None

    @classmethod
    def open(cls, path: Path) -> "Document":
        loaded = imageio.load_image(Path(path))
        return cls(loaded.image, path, loaded.format, loaded.icc_profile)

    # ------------------------------------------------------------ state
    @property
    def size(self) -> Tuple[int, int]:
        return self.image.size

    @property
    def modified(self) -> bool:
        return self.state_id != self.saved_state_id

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else "Untitled"

    def _snapshot(self, label: str) -> Snapshot:
        return Snapshot(self.image, tuple(s.copy() for s in self.shapes), label, self.state_id)

    def checkpoint(self, label: str, coalesce: Optional[str] = None) -> None:
        """Remember the current state so the change about to happen can be
        undone as `label`. Consecutive checkpoints with the same label and
        `coalesce` key merge into one undo step (arrow-key nudges, clicking a
        spin button repeatedly)."""
        key = (label, coalesce) if coalesce is not None else None
        if key is None or key != self._coalesce:
            self.history.push(self._snapshot(label))
        self._coalesce = key
        self.state_id = next(self._states)

    def cancel_checkpoint(self) -> None:
        """Forget the last checkpoint - only valid if nothing changed since."""
        snapshot = self.history.pop_last()
        if snapshot is not None:
            self.state_id = snapshot.state_id
        self._coalesce = None

    def _restore(self, snapshot: Snapshot) -> None:
        self.image = snapshot.image
        self.shapes = [s.copy() for s in snapshot.shapes]
        self.state_id = snapshot.state_id
        self._coalesce = None

    def undo(self) -> bool:
        snapshot = self.history.undo(self._snapshot(""))
        if snapshot is None:
            return False
        self._restore(snapshot)
        return True

    def redo(self) -> bool:
        snapshot = self.history.redo(self._snapshot(""))
        if snapshot is None:
            return False
        self._restore(snapshot)
        return True

    def break_coalescing(self) -> None:
        """The next checkpoint starts a new undo step whatever its label."""
        self._coalesce = None

    # ------------------------------------------------------------ shapes
    def find(self, shape_id: int) -> Optional[Shape]:
        return next((s for s in self.shapes if s.id == shape_id), None)

    def find_all(self, ids: Iterable[int]) -> List[Shape]:
        wanted = set(ids)
        return [s for s in self.shapes if s.id in wanted]

    def shape_at(self, x: float, y: float, tolerance: float) -> Optional[Shape]:
        """Topmost shape under (x, y)."""
        return next((s for s in reversed(self.shapes) if s.hit(x, y, tolerance)), None)

    def add_shape(self, shape: Shape) -> Shape:
        self.checkpoint(f"Add {shape.LABEL.lower()}")
        self.shapes.append(shape)
        return shape

    def remove_shapes(self, ids: Iterable[int]) -> int:
        wanted = set(ids)
        if not any(s.id in wanted for s in self.shapes):
            return 0
        self.checkpoint("Delete")
        before = len(self.shapes)
        self.shapes = [s for s in self.shapes if s.id not in wanted]
        return before - len(self.shapes)

    def duplicate_shapes(self, ids: Iterable[int], offset: float = 10) -> List[int]:
        originals = self.find_all(ids)
        if not originals:
            return []
        self.checkpoint("Duplicate")
        new_ids = []
        for shape in originals:
            clone = shape.copy()
            clone.id = new_id()
            clone.move(offset, offset)
            self.shapes.append(clone)
            new_ids.append(clone.id)
        return new_ids

    def move_shapes(self, ids: Sequence[int], dx: float, dy: float, coalesce: Optional[str] = None) -> None:
        shapes = self.find_all(ids)
        if not shapes or (dx == 0 and dy == 0):
            return
        self.checkpoint("Move", coalesce)
        for shape in shapes:
            shape.move(dx, dy)

    def reorder(self, ids: Iterable[int], to_front: bool) -> None:
        wanted = set(ids)
        picked = [s for s in self.shapes if s.id in wanted]
        rest = [s for s in self.shapes if s.id not in wanted]
        new_order = rest + picked if to_front else picked + rest
        if not picked or [s.id for s in new_order] == [s.id for s in self.shapes]:
            return
        self.checkpoint("Bring to front" if to_front else "Send to back")
        self.shapes = new_order

    def set_style(self, ids: Iterable[int], name: str, value) -> bool:
        """Apply one style field to the shapes it applies to. Repeated edits
        of the same field merge into one undo step."""
        targets = [s for s in self.find_all(ids) if name in s.STYLE_FIELDS and getattr(s, name) != value]
        if not targets:
            return False
        self.checkpoint(f"Change {name.replace('_', ' ')}", coalesce=f"{sorted(s.id for s in targets)}")
        for shape in targets:
            shape.apply_style(name, value)
        return True

    def edit_text(self, shape_id: int, text: str) -> bool:
        shape = self.find(shape_id)
        if shape is None or getattr(shape, "text", None) in (None, text):
            return False
        self.checkpoint("Edit text")
        shape.text = text  # type: ignore[attr-defined]
        return True

    # ------------------------------------------------------------ pixels
    def clamp_rect(self, rect: Tuple[float, float, float, float]) -> Optional[Tuple[int, int, int, int]]:
        """`rect` (any corner order) as whole pixels inside the image, or
        None if that leaves nothing."""
        x0, x1 = sorted((rect[0], rect[2]))
        y0, y1 = sorted((rect[1], rect[3]))
        w, h = self.size
        box = (max(0, int(round(x0))), max(0, int(round(y0))), min(w, int(round(x1))), min(h, int(round(y1))))
        return box if box[2] > box[0] and box[3] > box[1] else None

    def crop(self, rect: Tuple[float, float, float, float]) -> bool:
        box = self.clamp_rect(rect)
        if box is None or box == (0, 0) + self.size:
            return False
        self.checkpoint("Crop")
        self.image = self.image.crop(box)
        for shape in self.shapes:
            shape.move(-box[0], -box[1])
        return True

    def resize(self, width: int, height: int, resample=Image.Resampling.LANCZOS) -> bool:
        if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
            raise ValueError(f"Size must be between 1 and {MAX_DIMENSION} pixels")
        if (width, height) == self.size:
            return False
        sx, sy = width / self.image.width, height / self.image.height
        self.checkpoint("Resize")
        self.image = self.image.resize((width, height), resample)
        for shape in self.shapes:
            shape.scale(sx, sy)
        return True

    def flatten(self) -> bool:
        if not self.shapes:
            return False
        self.checkpoint("Flatten")
        self.image = composite(self.image, self.shapes)
        self.shapes = []
        return True

    # ------------------------------------------------------------ files
    def rendered(self) -> Image.Image:
        return composite(self.image, self.shapes)

    def save(self, path: Path, jpeg_quality: int = 90, png_compress_level: int = 6) -> None:
        imageio.save_image(self.rendered(), Path(path), jpeg_quality, png_compress_level, self.icc_profile)
        self.path = Path(path)
        self.source_format = imageio.save_format_for(self.path)
        self.saved_state_id = self.state_id
        self._coalesce = None
