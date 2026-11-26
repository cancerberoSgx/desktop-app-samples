"""The drawing area: shows the document through a `Viewport` (zoom/pan) and
turns mouse events into tool calls in image coordinates.

Pixels (image + shapes) come from `render.ViewRenderer` - the same Pillow
code that writes the saved file - rendered in *device* pixels so HiDPI
screens get full resolution. Decorations that are not part of the image
(selection boxes, handles, the crop frame, the marquee) are drawn on top with
wx in logical coordinates.

Zoom: Ctrl+wheel (around the pointer), View menu, +/- keys. Pan: wheel
(Shift for horizontal), middle-drag, Space+drag, or the Hand tool.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional

import wx

from app.editor.editor import Editor
from app.editor.render import ViewRenderer
from app.editor.tools import Mods
from app.editor.viewport import Viewport, wheel_zoom_factor

WHEEL_PAN_PX = 60
HANDLE_HALF = 4


@dataclass(frozen=True)
class CanvasPalette:
    background: wx.Colour
    border: wx.Colour
    hint: wx.Colour
    accent: wx.Colour
    shade: wx.Colour   # outside the crop area


LIGHT = CanvasPalette(wx.Colour(222, 222, 224), wx.Colour(160, 160, 165), wx.Colour(110, 110, 115),
                      wx.Colour(0, 120, 215), wx.Colour(0, 0, 0, 110))
DARK = CanvasPalette(wx.Colour(38, 38, 41), wx.Colour(80, 80, 86), wx.Colour(150, 150, 156),
                     wx.Colour(64, 156, 255), wx.Colour(0, 0, 0, 140))

_CURSORS = {
    "arrow": wx.CURSOR_ARROW, "cross": wx.CURSOR_CROSS, "hand": wx.CURSOR_HAND, "move": wx.CURSOR_SIZING,
    "ibeam": wx.CURSOR_IBEAM, "size_nwse": wx.CURSOR_SIZENWSE, "size_nesw": wx.CURSOR_SIZENESW,
    "size_ns": wx.CURSOR_SIZENS, "size_we": wx.CURSOR_SIZEWE,
}


def _mods(event: wx.MouseEvent) -> Mods:
    return Mods(shift=event.ShiftDown(), ctrl=event.ControlDown(), alt=event.AltDown())


class _FileDrop(wx.FileDropTarget):
    def __init__(self, on_files: Callable[[List[str]], None]) -> None:
        super().__init__()
        self._on_files = on_files

    def OnDropFiles(self, x, y, filenames):  # noqa: N802 - wx API
        wx.CallAfter(self._on_files, list(filenames))
        return True


class Canvas(wx.Panel):
    def __init__(self, parent: wx.Window, editor: Editor,
                 on_pointer: Callable[[Optional[tuple]], None],
                 on_view_changed: Callable[[], None],
                 on_files_dropped: Callable[[List[str]], None],
                 on_empty_click: Callable[[], None]) -> None:
        super().__init__(parent, style=wx.WANTS_CHARS | wx.FULL_REPAINT_ON_RESIZE | wx.BORDER_NONE)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.editor = editor
        self.viewport = Viewport()
        self.palette = LIGHT
        self.space_down = False
        self._renderer = ViewRenderer()
        self._on_pointer = on_pointer
        self._on_view_changed = on_view_changed
        self._on_empty_click = on_empty_click
        self._pan_from: Optional[wx.Point] = None
        self._tool_dragging = False
        self._doc = None

        self.SetDropTarget(_FileDrop(on_files_dropped))
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self.Bind(wx.EVT_LEFT_DCLICK, self._on_left_dclick)
        self.Bind(wx.EVT_MIDDLE_DOWN, self._on_middle_down)
        self.Bind(wx.EVT_MIDDLE_UP, self._on_middle_up)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_MOUSEWHEEL, self._on_wheel)
        self.Bind(wx.EVT_LEAVE_WINDOW, lambda e: (self._on_pointer(None), e.Skip()))
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._on_capture_lost)
        self.Bind(wx.EVT_KEY_UP, self._on_key_up)
        self.Bind(wx.EVT_KILL_FOCUS, self._on_kill_focus)
        editor.subscribe(self._on_editor)

    # ------------------------------------------------------------ view
    def _scale(self) -> float:
        return self.GetContentScaleFactor()

    def _sync_sizes(self) -> None:
        doc = self.editor.doc
        size = self.GetClientSize()
        self.viewport.set_sizes(doc.size if doc else (0, 0), (size.width, size.height))

    def _view_changed(self) -> None:
        self.editor.set_zoom(self.viewport.zoom)
        self.Refresh()
        self._on_view_changed()

    def reset_view(self, fit: bool) -> None:
        """New document: fit it (never enlarging) or show it at 100%."""
        self._sync_sizes()
        if fit:
            self.viewport.fit()
        else:
            self.viewport.zoom = 1.0
            self.viewport.clamp()
        self._view_changed()

    def zoom_step(self, direction: int) -> None:
        self.viewport.step_zoom(direction)
        self._view_changed()

    def zoom_fit(self) -> None:
        self._sync_sizes()
        self.viewport.fit(allow_upscale=True)
        self._view_changed()

    def zoom_actual(self) -> None:
        self.viewport.actual_size()
        self._view_changed()

    def set_dark(self, dark: bool) -> None:
        palette = DARK if dark else LIGHT
        if palette is not self.palette:
            self.palette = palette
            self.Refresh()

    # ------------------------------------------------------------ editor events
    def _on_editor(self, what: str) -> None:
        if what == "document":
            doc = self.editor.doc
            if doc is not self._doc:
                self._doc = doc  # the frame calls reset_view for new documents
            elif doc is not None and doc.size != self.viewport.image_size:
                self._sync_sizes()  # cropped / resized: re-centre if it now fits
                self._on_view_changed()
        self.Refresh()

    # ------------------------------------------------------------ painting
    def _on_size(self, event: wx.SizeEvent) -> None:
        event.Skip()
        self._sync_sizes()
        self.Refresh()

    def _on_paint(self, event: wx.PaintEvent) -> None:
        # What AutoBufferedPaintDC does, but as a concrete DC type wx.GCDC
        # accepts: the platform double-buffers (GTK3, macOS) -> PaintDC, which
        # also keeps HiDPI crisp; otherwise draw into a buffer.
        dc = wx.PaintDC(self) if self.IsDoubleBuffered() else wx.BufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.palette.background))
        dc.Clear()
        doc = self.editor.doc
        if doc is None:
            self._paint_empty(dc)
            return
        scale = self._scale()
        size = self.GetClientSize()
        shapes = list(doc.shapes)
        preview = self.editor.preview_shape()
        if preview is not None:
            shapes.append(preview)
        rendered = self._renderer.render(
            doc.image, shapes, self.viewport.zoom * scale, self.viewport.device_origin(scale),
            (int(size.width * scale), int(size.height * scale)))
        if rendered is not None:
            image, (x, y) = rendered
            bitmap = wx.Bitmap.FromBuffer(image.width, image.height, image.tobytes())
            if scale != 1 and hasattr(bitmap, "SetScaleFactor"):
                bitmap.SetScaleFactor(scale)
            dc.DrawBitmap(bitmap, int(round(x / scale)), int(round(y / scale)))
        # GCDC: anti-aliased, and honours the alpha of the shade brushes.
        self._paint_overlays(wx.GCDC(dc))

    def _paint_empty(self, dc: wx.DC) -> None:
        text = "Open an image (Ctrl+O), drop one here, or click to browse"
        dc.SetTextForeground(self.palette.hint)
        font = self.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        dc.SetFont(font)
        tw, th = dc.GetTextExtent(text)
        size = self.GetClientSize()
        dc.DrawText(text, (size.width - tw) // 2, (size.height - th) // 2)

    def _screen_rect(self, rect) -> wx.Rect:
        x0, y0, x1, y1 = self.viewport.rect_to_screen(rect)
        return wx.Rect(int(round(x0)), int(round(y0)), max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0))))

    def _paint_handles(self, dc: wx.DC, handles) -> None:
        dc.SetPen(wx.Pen(self.palette.accent, 1))
        dc.SetBrush(wx.WHITE_BRUSH)
        for _name, (hx, hy) in handles:
            sx, sy = self.viewport.to_screen(hx, hy)
            dc.DrawRectangle(int(round(sx)) - HANDLE_HALF, int(round(sy)) - HANDLE_HALF,
                             2 * HANDLE_HALF + 1, 2 * HANDLE_HALF + 1)

    def _paint_overlays(self, dc: wx.DC) -> None:
        doc, editor, p = self.editor.doc, self.editor, self.palette
        w, h = doc.size
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.SetPen(wx.Pen(p.border, 1))
        dc.DrawRectangle(self._screen_rect((0, 0, w, h)).Inflate(1, 1))

        selected = editor.selected_shapes()
        dashed = wx.Pen(p.accent, 1, wx.PENSTYLE_SHORT_DASH)
        for shape in selected:
            dc.SetPen(dashed)
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.DrawRectangle(self._screen_rect(shape.bounds()).Inflate(2, 2))
        if len(selected) == 1:
            self._paint_handles(dc, selected[0].handles())

        marquee = getattr(editor.tool, "marquee", None)
        if marquee:
            dc.SetPen(dashed)
            dc.SetBrush(wx.Brush(wx.Colour(p.accent.Red(), p.accent.Green(), p.accent.Blue(), 40)))
            dc.DrawRectangle(self._screen_rect(marquee))

        crop = editor.crop_tool
        if editor.tool is crop and crop.rect:
            self._paint_crop(dc, crop.rect, crop.handles())

    def _paint_crop(self, dc: wx.DC, rect, handles) -> None:
        w, h = self.editor.doc.size
        image = self._screen_rect((0, 0, w, h))
        keep = self._screen_rect(rect)
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(self.palette.shade))
        # Shade the four bands around the kept area.
        for band in (wx.Rect(image.x, image.y, image.width, keep.y - image.y),
                     wx.Rect(image.x, keep.GetBottom() + 1, image.width, image.GetBottom() - keep.GetBottom()),
                     wx.Rect(image.x, keep.y, keep.x - image.x, keep.height),
                     wx.Rect(keep.GetRight() + 1, keep.y, image.GetRight() - keep.GetRight(), keep.height)):
            if band.width > 0 and band.height > 0:
                dc.DrawRectangle(band)
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.SetPen(wx.Pen(wx.WHITE, 1))
        dc.DrawRectangle(keep)
        dc.SetPen(wx.Pen(wx.Colour(0, 0, 0), 1, wx.PENSTYLE_SHORT_DASH))
        dc.DrawRectangle(keep)
        self._paint_handles(dc, handles)
        label = f"{rect[2] - rect[0]} × {rect[3] - rect[1]}"
        tw, th = dc.GetTextExtent(label)
        lx, ly = keep.x + 4, keep.y - th - 6 if keep.y - th - 6 > 0 else keep.y + 4
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(wx.Colour(0, 0, 0, 170)))
        dc.DrawRoundedRectangle(lx - 3, ly - 1, tw + 6, th + 2, 3)
        dc.SetTextForeground(wx.WHITE)
        dc.DrawText(label, lx, ly)

    # ------------------------------------------------------------ mouse
    def _image_point(self, event: wx.MouseEvent):
        pos = event.GetPosition()
        return self.viewport.to_image(pos.x, pos.y)

    def _start_pan(self, event: wx.MouseEvent) -> None:
        self._pan_from = event.GetPosition()
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        if not self.HasCapture():
            self.CaptureMouse()

    def _end_pan(self) -> None:
        self._pan_from = None
        if self.HasCapture():
            self.ReleaseMouse()

    def _on_left_down(self, event: wx.MouseEvent) -> None:
        self.SetFocus()
        if self.editor.doc is None:
            self._on_empty_click()
            return
        if self.editor.tool.PANS or self.space_down:
            self._start_pan(event)
            return
        self._tool_dragging = True
        if not self.HasCapture():
            self.CaptureMouse()
        self.editor.tool.press(*self._image_point(event), _mods(event))

    def _on_left_up(self, event: wx.MouseEvent) -> None:
        if self._pan_from is not None:
            self._end_pan()
            self._update_cursor(event)
            return
        if not self._tool_dragging:
            return
        self._tool_dragging = False
        if self.HasCapture():
            self.ReleaseMouse()
        self.editor.tool.release(*self._image_point(event), _mods(event))
        self._update_cursor(event)

    def _on_left_dclick(self, event: wx.MouseEvent) -> None:
        if self.editor.doc is not None and not self.editor.tool.PANS:
            self.editor.tool.double_click(*self._image_point(event), _mods(event))

    def _on_middle_down(self, event: wx.MouseEvent) -> None:
        if self.editor.doc is not None:
            self._start_pan(event)

    def _on_middle_up(self, event: wx.MouseEvent) -> None:
        if self._pan_from is not None:
            self._end_pan()
            self._update_cursor(event)

    def _on_motion(self, event: wx.MouseEvent) -> None:
        if self.editor.doc is None:
            return
        x, y = self._image_point(event)
        w, h = self.editor.doc.size
        self._on_pointer((int(x), int(y)) if 0 <= x < w and 0 <= y < h else None)
        if self._pan_from is not None:
            pos = event.GetPosition()
            self.viewport.pan(pos.x - self._pan_from.x, pos.y - self._pan_from.y)
            self._pan_from = pos
            self._view_changed()
        elif self._tool_dragging and event.LeftIsDown():
            self.editor.tool.drag(x, y, _mods(event))
        else:
            self._update_cursor(event)

    def _update_cursor(self, event: Optional[wx.MouseEvent] = None) -> None:
        if self.editor.doc is None:
            self.SetCursor(wx.Cursor(wx.CURSOR_ARROW))
            return
        if self.space_down or self.editor.tool.PANS:
            self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            return
        pos = event.GetPosition() if event is not None else self.ScreenToClient(wx.GetMousePosition())
        name = self.editor.tool.cursor_at(*self.viewport.to_image(pos.x, pos.y))
        self.SetCursor(wx.Cursor(_CURSORS.get(name, wx.CURSOR_ARROW)))

    def _on_wheel(self, event: wx.MouseEvent) -> None:
        if self.editor.doc is None:
            return
        rotation, delta = event.GetWheelRotation(), event.GetWheelDelta() or 120
        if event.ControlDown():
            pos = event.GetPosition()
            self.viewport.zoom_by(wheel_zoom_factor(rotation, delta), (pos.x, pos.y))
        else:
            amount = rotation / delta * WHEEL_PAN_PX
            horizontal = event.GetWheelAxis() == wx.MOUSE_WHEEL_HORIZONTAL or event.ShiftDown()
            if event.GetWheelAxis() == wx.MOUSE_WHEEL_HORIZONTAL:
                amount = -amount  # horizontal axis reports right as positive
            self.viewport.pan(amount, 0) if horizontal else self.viewport.pan(0, amount)
        self._view_changed()

    def _on_capture_lost(self, event: wx.MouseCaptureLostEvent) -> None:
        self._pan_from = None
        if self._tool_dragging:
            self._tool_dragging = False
            self.editor.tool.cancel()
            self.editor.notify("document")

    # ------------------------------------------------------------ keys
    def set_space_down(self, down: bool) -> None:
        if down != self.space_down:
            self.space_down = down
            self._update_cursor()

    def _on_key_up(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_SPACE:
            self.set_space_down(False)
        event.Skip()

    def _on_kill_focus(self, event: wx.FocusEvent) -> None:
        self.set_space_down(False)
        event.Skip()
