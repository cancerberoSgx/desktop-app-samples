from app.editor.editor import Editor
from app.editor.shapes import ArrowShape, EllipseShape, RectShape, Style, TextShape
from app.editor.tools import Mods, NO_MODS

SHIFT = Mods(shift=True)


def drag(ed: Editor, start, end, mods=NO_MODS):
    ed.tool.press(*start, mods)
    ed.tool.drag(*end, mods)
    ed.tool.release(*end, mods)


def test_draw_rect_uses_style_and_selects_it(editor: Editor):
    editor.style = Style(color=(1, 2, 3, 255), width=6)
    editor.set_tool("rect")
    drag(editor, (50, 40), (10, 10))
    shape = editor.doc.shapes[0]
    assert isinstance(shape, RectShape)
    assert (shape.x0, shape.y0, shape.x1, shape.y1) == (10, 10, 50, 40)  # normalized
    assert shape.color == (1, 2, 3, 255) and shape.width == 6
    assert editor.selection == [shape.id]


def test_click_without_drag_draws_nothing(editor: Editor):
    editor.set_tool("ellipse")
    drag(editor, (10, 10), (11, 11))
    assert editor.doc.shapes == [] and not editor.doc.modified


def test_shift_constrains_shapes(editor: Editor):
    editor.set_tool("ellipse")
    drag(editor, (10, 10), (50, 20), SHIFT)
    e = editor.doc.shapes[0]
    assert isinstance(e, EllipseShape) and (e.x1 - e.x0) == (e.y1 - e.y0) == 40
    editor.set_tool("arrow")
    drag(editor, (10, 10), (100, 14), SHIFT)
    a = editor.doc.shapes[1]
    assert isinstance(a, ArrowShape) and a.y1 == 10


def test_select_move_and_undo(editor: Editor):
    rect = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=50, y1=50, width=4))
    editor.set_tool("select")
    drag(editor, (10, 30), (30, 35))       # grab the left edge
    assert (rect.x0, rect.y0) == (30, 15) and editor.selection == [rect.id]
    assert editor.doc.history.undo_label() == "Move"
    editor.undo()
    assert editor.doc.shapes[0].x0 == 10
    assert editor.selection == [rect.id]   # same id survives undo


def test_click_on_shape_without_moving_adds_no_undo_step(editor: Editor):
    editor.doc.add_shape(RectShape(x0=10, y0=10, x1=50, y1=50))
    steps = len(editor.doc.history._undo)
    drag(editor, (10, 30), (10, 30))
    assert len(editor.doc.history._undo) == steps


def test_resize_with_handle(editor: Editor):
    rect = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=50, y1=50))
    editor.select([rect.id])
    drag(editor, (50, 50), (80, 70))
    assert (rect.x1, rect.y1) == (80, 70)
    assert editor.doc.history.undo_label() == "Resize"


def test_marquee_and_shift_click_selection(editor: Editor):
    a = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=20, y1=20))
    b = editor.doc.add_shape(RectShape(x0=100, y0=10, x1=120, y1=20))
    drag(editor, (0, 0), (30, 30))
    assert editor.selection == [a.id]
    editor.tool.press(100, 15, SHIFT)
    editor.tool.release(100, 15, SHIFT)
    assert editor.selection == [a.id, b.id]
    drag(editor, (150, 80), (160, 90))     # empty space clears
    assert editor.selection == []


def test_nudge_delete_duplicate_reorder(editor: Editor):
    a = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=20, y1=20))
    b = editor.doc.add_shape(RectShape(x0=30, y0=10, x1=40, y1=20))
    editor.select([a.id])
    assert editor.key("right", SHIFT) and a.x0 == 20
    editor.reorder_selection(to_front=True)
    assert [s.id for s in editor.doc.shapes] == [b.id, a.id]
    editor.duplicate_selection()
    assert len(editor.doc.shapes) == 3 and editor.selection != [a.id]
    assert editor.key("delete", NO_MODS) and len(editor.doc.shapes) == 2


def test_sidebar_style_applies_to_selection_and_new_shapes(editor: Editor):
    rect = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=20, y1=20, width=2))
    editor.select([rect.id])
    editor.set_style_value("width", 9)
    assert rect.width == 9 and editor.style.width == 9
    editor.set_style_value("font_size", 70)   # not a rect field: style only
    assert editor.style.font_size == 70


def test_selecting_picks_up_the_shape_style(editor: Editor):
    rect = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=20, y1=20, width=11, fill=(5, 5, 5, 255)))
    editor.select([rect.id])
    assert editor.style.width == 11 and editor.style.fill == (5, 5, 5, 255)


def test_text_tool_adds_and_edits(editor: Editor):
    answers = iter(["Hello", "Bye", ""])
    editor.request_text = lambda initial: next(answers)
    editor.set_tool("text")
    editor.tool.release(20, 20, NO_MODS)
    text = editor.doc.shapes[0]
    assert isinstance(text, TextShape) and text.text == "Hello"
    editor.tool.release(22, 25, NO_MODS)   # click on it: edit
    assert text.text == "Bye"
    editor.tool.release(22, 25, NO_MODS)   # emptied: removed
    assert editor.doc.shapes == []


def test_text_tool_cancel_adds_nothing(editor: Editor):
    editor.request_text = lambda initial: None
    editor.set_tool("text")
    editor.tool.release(20, 20, NO_MODS)
    assert editor.doc.shapes == [] and not editor.doc.modified


def test_crop_tool(editor: Editor):
    editor.doc.add_shape(RectShape(x0=60, y0=30, x1=70, y1=40))
    editor.set_tool("crop")
    drag(editor, (50, 20), (250, 90))       # clamped to the 200x100 image
    assert editor.crop_tool.rect == (50, 20, 200, 90)
    drag(editor, (100, 50), (90, 50))       # move it
    assert editor.crop_tool.rect == (40, 20, 190, 90)
    assert editor.key("enter", NO_MODS)
    assert editor.doc.size == (150, 70) and editor.crop_tool.rect is None
    assert editor.tool.NAME == "select"
    assert (editor.doc.shapes[0].x0, editor.doc.shapes[0].y0) == (20, 10)
    editor.undo()
    assert editor.doc.size == (200, 100)


def test_crop_escape_and_tool_switch_clear_rect(editor: Editor):
    editor.set_tool("crop")
    drag(editor, (10, 10), (50, 50))
    assert editor.key("escape", NO_MODS) and editor.crop_tool.rect is None
    drag(editor, (10, 10), (50, 50))
    editor.set_tool("select")
    assert editor.crop_tool.rect is None


def test_zoom_scales_hit_tolerance(editor: Editor):
    rect = editor.doc.add_shape(RectShape(x0=10, y0=10, x1=50, y1=50, width=1))
    editor.set_zoom(0.1)          # zoomed out: 6 screen px = 60 image px
    assert editor.doc.shape_at(30, 30, editor.tolerance) is rect
    editor.set_zoom(4)
    assert editor.doc.shape_at(30, 30, editor.tolerance) is None


def test_listeners_are_notified(editor: Editor):
    seen = []
    editor.subscribe(seen.append)
    editor.set_tool("rect")
    drag(editor, (10, 10), (40, 40))
    assert {"tool", "overlay", "document", "selection"} <= set(seen)
