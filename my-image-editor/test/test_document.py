import pytest
from PIL import Image

from app.editor.document import Document
from app.editor.imageio import load_image, save_image, with_save_extension
from app.editor.shapes import RectShape, TextShape
from conftest import BLUE, RED, WHITE, blank


def test_new_document_is_unmodified(doc: Document):
    assert not doc.modified and not doc.history.can_undo()


def test_add_undo_redo(doc: Document):
    rect = doc.add_shape(RectShape(x0=1, y0=1, x1=10, y1=10))
    assert doc.modified and doc.history.undo_label() == "Add rectangle"
    assert doc.undo() and doc.shapes == [] and not doc.modified
    assert doc.history.redo_label() == "Add rectangle"
    assert doc.redo() and [s.id for s in doc.shapes] == [rect.id]
    assert doc.modified
    assert not doc.redo()


def test_new_change_clears_redo(doc: Document):
    doc.add_shape(RectShape())
    doc.undo()
    doc.add_shape(RectShape())
    assert not doc.history.can_redo()


def test_history_holds_copies_not_live_shapes(doc: Document):
    rect = doc.add_shape(RectShape(x0=0, y0=0, x1=10, y1=10))
    doc.move_shapes([rect.id], 5, 5)
    rect.x0 = 999  # mutating the live shape must not change history
    doc.undo()
    assert doc.shapes[0].x0 == 0


def test_cancel_checkpoint_restores_unmodified(doc: Document):
    doc.checkpoint("Move")
    assert doc.modified
    doc.cancel_checkpoint()
    assert not doc.modified and not doc.history.can_undo()


def test_nudges_coalesce_into_one_step(doc: Document):
    rect = doc.add_shape(RectShape(x0=0, y0=0, x1=10, y1=10))
    for _ in range(5):
        doc.move_shapes([rect.id], 1, 0, coalesce="nudge")
    assert doc.shapes[0].x0 == 5
    doc.undo()
    assert doc.shapes[0].x0 == 0
    assert doc.history.undo_label() == "Add rectangle"


def test_style_change_coalesces_per_field(doc: Document):
    rect = doc.add_shape(RectShape(width=2))
    doc.set_style([rect.id], "width", 3)
    doc.set_style([rect.id], "width", 4)
    doc.set_style([rect.id], "color", BLUE)
    doc.undo()
    assert doc.shapes[0].color != BLUE and doc.shapes[0].width == 4
    doc.undo()
    assert doc.shapes[0].width == 2
    assert not doc.set_style([rect.id], "font_size", 10)  # doesn't apply to rects


def test_modified_tracks_save_point_through_undo(doc: Document, tmp_path):
    doc.add_shape(RectShape(x0=1, y0=1, x1=5, y1=5))
    doc.save(tmp_path / "a.png")
    assert not doc.modified
    doc.undo()
    assert doc.modified
    doc.redo()
    assert not doc.modified


def test_crop_moves_shapes_and_undoes(doc: Document):
    rect = doc.add_shape(RectShape(x0=50, y0=20, x1=60, y1=30))
    assert doc.crop((40, 10, 140, 60))
    assert doc.size == (100, 50)
    assert (doc.shapes[0].x0, doc.shapes[0].y0) == (10, 10)
    doc.undo()
    assert doc.size == (200, 100) and doc.find(rect.id).x0 == 50


def test_crop_is_clamped_and_rejects_empty(doc: Document):
    assert doc.clamp_rect((150, 80, 400, -20)) == (150, 0, 200, 80)
    assert not doc.crop((300, 300, 400, 400))
    assert not doc.crop((0, 0, 200, 100))  # whole image: nothing to do
    assert not doc.history.can_undo()


def test_resize_scales_shapes(doc: Document):
    doc.add_shape(RectShape(x0=20, y0=10, x1=40, y1=30, width=4))
    doc.add_shape(TextShape(x=10, y=10, text="a", font_size=20))
    assert doc.resize(100, 50)
    rect, text = doc.shapes
    assert (rect.x0, rect.y1, rect.width) == (10, 15, 2)
    assert (text.x, text.font_size) == (5, 10)
    with pytest.raises(ValueError):
        doc.resize(0, 10)


def test_flatten_bakes_shapes_into_pixels(doc: Document):
    doc.add_shape(RectShape(x0=0, y0=0, x1=200, y1=100, width=1, fill=RED))
    assert doc.flatten()
    assert doc.shapes == [] and doc.image.getpixel((100, 50)) == RED
    doc.undo()
    assert doc.image.getpixel((100, 50)) == WHITE and len(doc.shapes) == 1


def test_save_renders_shapes_but_keeps_them_editable(doc: Document, tmp_path):
    doc.add_shape(RectShape(x0=0, y0=0, x1=200, y1=100, width=1, fill=RED))
    doc.save(tmp_path / "out.png")
    assert len(doc.shapes) == 1 and doc.image.getpixel((100, 50)) == WHITE
    saved = Image.open(tmp_path / "out.png")
    assert saved.mode == "RGB" and saved.getpixel((100, 50)) == (255, 0, 0)


def test_jpeg_flattens_alpha_on_white(tmp_path):
    path = tmp_path / "t.jpg"
    save_image(blank(10, 10, (0, 0, 0, 0)), path, jpeg_quality=95)
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.mode == "RGB"
        assert all(c > 245 for c in im.getpixel((5, 5)))


def test_png_keeps_alpha_and_round_trips(tmp_path):
    img = blank(10, 10)
    img.putpixel((0, 0), (0, 0, 0, 0))
    save_image(img, tmp_path / "t.png")
    loaded = load_image(tmp_path / "t.png")
    assert loaded.format == "PNG" and loaded.image.getpixel((0, 0))[3] == 0


def test_save_rejects_unknown_extension_and_leaves_no_temp(tmp_path):
    with pytest.raises(ValueError):
        save_image(blank(), tmp_path / "x.bmp")
    assert list(tmp_path.iterdir()) == []


def test_with_save_extension(tmp_path):
    assert with_save_extension(tmp_path / "a.JPG").name == "a.JPG"
    assert with_save_extension(tmp_path / "a", 1).name == "a.jpg"
    assert with_save_extension(tmp_path / "a.bmp", 0).name == "a.bmp.png"


def test_load_applies_exif_orientation(tmp_path):
    img = Image.new("RGB", (40, 20), (255, 255, 255))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    img.save(tmp_path / "r.jpg", exif=exif)
    loaded = load_image(tmp_path / "r.jpg")
    assert loaded.image.size == (20, 40) and loaded.image.mode == "RGBA"


def test_open_document(tmp_path):
    blank(30, 20).save(tmp_path / "p.png")
    doc = Document.open(tmp_path / "p.png")
    assert doc.size == (30, 20) and doc.source_format == "PNG" and doc.display_name == "p.png"
