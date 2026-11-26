"""The editing session the UI talks to: the open document, which shapes are
selected, the current style and tool. wx-free; the UI subscribes with
`subscribe(callback)` and is told what changed:

- "document": pixels or shapes changed (also during a drag), or a new
  document / undo / redo.
- "selection", "style", "tool": what the names say.
- "overlay": only canvas decorations changed (shape being drawn, crop or
  marquee rectangle) - nothing to save.

The few things that need UI (asking for text) are callbacks the frame sets.
"""
from dataclasses import replace
from typing import Callable, Dict, List, Optional

from app.editor.document import Document
from app.editor.shapes import Shape, Style, TextShape
from app.editor.tools import TOOL_CLASSES, CropTool, Mods, Tool

HIT_TOLERANCE_PX = 6   # screen pixels
HANDLE_REACH_PX = 7    # screen pixels
MIN_DRAG_PX = 3        # screen pixels: smaller drags are clicks
NUDGE, BIG_NUDGE = 1, 10


class Editor:
    def __init__(self, style: Optional[Style] = None) -> None:
        self.doc: Optional[Document] = None
        self.selection: List[int] = []
        self.style = style or Style()
        self.tools: Dict[str, Tool] = {cls.NAME: cls(self) for cls in TOOL_CLASSES}
        self.tool: Tool = self.tools["select"]
        self._listeners: List[Callable[[str], None]] = []
        # Distances in image pixels; the canvas updates them on every zoom.
        self.tolerance = float(HIT_TOLERANCE_PX)
        self.handle_reach = float(HANDLE_REACH_PX)
        self.min_drag = float(MIN_DRAG_PX)
        # Set by the UI: ask for a text shape's content (None = cancelled).
        self.request_text: Callable[[str], Optional[str]] = lambda initial: None

    # ------------------------------------------------------------ events
    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def notify(self, what: str) -> None:
        for listener in list(self._listeners):
            listener(what)

    def set_zoom(self, zoom: float) -> None:
        self.tolerance = HIT_TOLERANCE_PX / zoom
        self.handle_reach = HANDLE_REACH_PX / zoom
        self.min_drag = MIN_DRAG_PX / zoom

    # ------------------------------------------------------------ document
    def set_document(self, doc: Optional[Document]) -> None:
        self.tool.cancel()
        self.doc = doc
        self.selection = []
        self.notify("document")
        self.notify("selection")

    # ------------------------------------------------------------ tools
    def set_tool(self, name: str) -> None:
        if name not in self.tools or self.tools[name] is self.tool:
            return
        self.tool.cancel()
        self.tool = self.tools[name]
        self.notify("tool")
        self.notify("overlay")

    @property
    def crop_tool(self) -> CropTool:
        return self.tools["crop"]  # type: ignore[return-value]

    def preview_shape(self) -> Optional[Shape]:
        return self.tool.preview()

    # ------------------------------------------------------------ selection
    def selected_shapes(self) -> List[Shape]:
        return self.doc.find_all(self.selection) if self.doc else []

    def select(self, ids: List[int]) -> None:
        ids = list(dict.fromkeys(ids))
        if ids == self.selection:
            return
        self.selection = ids
        if self.doc:
            self.doc.break_coalescing()  # nudging another shape is a new undo step
        self._style_from_selection()
        self.notify("selection")

    def toggle_selected(self, shape_id: int) -> None:
        ids = [i for i in self.selection if i != shape_id]
        self.select(ids if len(ids) != len(self.selection) else self.selection + [shape_id])

    def select_all(self) -> None:
        if self.doc:
            self.select([s.id for s in self.doc.shapes])

    def _prune_selection(self) -> None:
        existing = {s.id for s in self.doc.shapes} if self.doc else set()
        kept = [i for i in self.selection if i in existing]
        if kept != self.selection:
            self.selection = kept
            self.notify("selection")

    def _style_from_selection(self) -> None:
        """The sidebar shows (and new shapes get) the style of what was just
        selected - selecting a shape is also the way to pick its style up."""
        shapes = self.selected_shapes()
        if not shapes:
            return
        values = shapes[-1].style_values()
        style = replace(self.style, **values)
        if style != self.style:
            self.style = style
            self.notify("style")

    # ------------------------------------------------------------ style
    def set_style_value(self, name: str, value) -> None:
        """Sidebar edit: becomes the style for new shapes and applies to the
        selected shapes it fits."""
        self.style = replace(self.style, **{name: value})
        self.notify("style")
        if self.doc and self.doc.set_style(self.selection, name, value):
            self.notify("document")

    # ------------------------------------------------------------ commands
    def _after_change(self) -> None:
        self._prune_selection()
        self.notify("document")

    def undo(self) -> None:
        if self.doc is None:
            return
        self.tool.cancel()
        if self.doc.undo():
            self._after_change()
        self.notify("overlay")

    def redo(self) -> None:
        if self.doc is None:
            return
        self.tool.cancel()
        if self.doc.redo():
            self._after_change()
        self.notify("overlay")

    def delete_selection(self) -> None:
        if self.doc and self.doc.remove_shapes(self.selection):
            self._after_change()

    def duplicate_selection(self) -> None:
        if self.doc:
            new_ids = self.doc.duplicate_shapes(self.selection)
            if new_ids:
                self.select(new_ids)
                self.notify("document")

    def reorder_selection(self, to_front: bool) -> None:
        if self.doc and self.selection:
            self.doc.reorder(self.selection, to_front)
            self.notify("document")

    def nudge(self, dx: int, dy: int) -> None:
        if self.doc and self.selection:
            self.doc.move_shapes(self.selection, dx, dy, coalesce="nudge")
            self.notify("document")

    def edit_text(self, shape: TextShape) -> None:
        if self.doc is None:
            return
        text = self.request_text(shape.text)
        if text is None:
            return
        if not text.strip():
            self.doc.remove_shapes([shape.id])
            self._after_change()
        elif self.doc.edit_text(shape.id, text):
            self.notify("document")

    def apply_crop(self) -> bool:
        tool = self.crop_tool
        if self.doc is None or tool.rect is None:
            return False
        changed = self.doc.crop(tool.rect)
        tool.cancel()
        self.notify("overlay")
        if changed:
            self._after_change()
            self.set_tool("select")  # cropping is usually a one-off step
        return changed

    def resize(self, width: int, height: int, resample) -> bool:
        if self.doc is None:
            return False
        self.tool.cancel()
        changed = self.doc.resize(width, height, resample)
        if changed:
            self._after_change()
        return changed

    def flatten(self) -> bool:
        if self.doc is None or not self.doc.flatten():
            return False
        self._after_change()
        return True

    def key(self, name: str, mods: Mods) -> bool:
        """Keys the canvas forwards when it has focus; True if handled."""
        if self.tool.key(name, mods):
            return True
        step = BIG_NUDGE if mods.shift else NUDGE
        arrows = {"left": (-step, 0), "right": (step, 0), "up": (0, -step), "down": (0, step)}
        if name in arrows and self.selection:
            self.nudge(*arrows[name])
            return True
        if name == "delete" and self.selection:
            self.delete_selection()
            return True
        if name == "escape" and self.selection:
            self.select([])
            return True
        return False
