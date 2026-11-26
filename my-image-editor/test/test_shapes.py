from app.editor import fonts
from app.editor.shapes import (ArrowShape, EllipseShape, LineShape, RectShape, Style, TextShape, shape_from_dict,
                               shape_from_style, snap_45)


def test_rect_hit_outline_vs_filled():
    r = RectShape(x0=10, y0=10, x1=110, y1=60, width=4)
    assert r.hit(10, 30, 2)          # on the left edge
    assert not r.hit(60, 35, 2)      # centre of an unfilled rect
    r.fill = (0, 0, 0, 255)
    assert r.hit(60, 35, 2)
    assert not r.hit(200, 35, 2)


def test_ellipse_hit():
    e = EllipseShape(x0=0, y0=0, x1=100, y1=50, width=2)
    assert e.hit(100, 25, 2) and e.hit(50, 0, 2)
    assert not e.hit(50, 25, 2)
    assert not e.hit(0, 0, 2)        # bounding-box corner is outside the ellipse


def test_line_hit_and_bounds():
    line = LineShape(x0=0, y0=0, x1=100, y1=100, width=4)
    assert line.hit(50, 52, 1) and not line.hit(50, 70, 1)
    assert line.bounds() == (-2, -2, 102, 102)
    arrow = ArrowShape(x0=0, y0=0, x1=100, y1=0, width=4)
    assert arrow.bounds()[1] < -2   # the head is wider than the line


def test_box_handle_drag_flips_name_when_inverted():
    r = RectShape(x0=10, y0=10, x1=50, y1=50)
    name = r.drag_handle("se", 0, 60)          # dragged left past the west edge
    assert name == "sw"
    assert (r.x0, r.y0, r.x1, r.y1) == (0, 10, 10, 60)
    assert r.drag_handle("n", 30, 80) == "s"
    assert (r.y0, r.y1) == (60, 80)


def test_box_corner_drag_constrained_is_square():
    r = RectShape(x0=0, y0=0, x1=10, y1=10)
    r.drag_handle("se", 40, 20, constrain=True)
    assert (r.x1 - r.x0, r.y1 - r.y0) == (40, 40)


def test_snap_45():
    x, y = snap_45(0, 0, 100, 10)
    assert (round(x), round(y)) == (100, 0)
    x, y = snap_45(0, 0, 50, 45)
    assert round(x) == round(y)


def test_move_and_scale():
    r = RectShape(x0=10, y0=20, x1=30, y1=40, width=4)
    r.move(5, -5)
    assert (r.x0, r.y0, r.x1, r.y1) == (15, 15, 35, 35)
    r.scale(2, 2)
    assert (r.x0, r.y1, r.width) == (30, 70, 8)
    t = TextShape(x=10, y=10, text="hi", font_size=20)
    t.scale(0.5, 0.5)
    assert (t.x, t.font_size) == (5, 10)


def test_text_bounds_grow_with_size_and_lines():
    small = TextShape(text="Hello", font_size=20).bounds()
    big = TextShape(text="Hello", font_size=40).bounds()
    two = TextShape(text="Hello\nWorld", font_size=20).bounds()
    assert big[2] > small[2] and big[3] > small[3]
    assert two[3] > small[3] * 1.5


def test_dict_round_trip_keeps_kind_and_style():
    for shape in (RectShape(x0=1, y0=2, x1=3, y1=4, fill=(1, 2, 3, 4)), ArrowShape(x1=9),
                  TextShape(text="x", bold=True, font_family=fonts.SERIF)):
        clone = shape_from_dict(shape.to_dict())
        assert type(clone) is type(shape)
        assert clone.to_dict() == shape.to_dict()
        assert clone.id != shape.id


def test_shape_from_style_takes_only_fitting_fields():
    style = Style(color=(1, 2, 3, 255), fill=(9, 9, 9, 255), width=7, font_size=50)
    line = shape_from_style(LineShape, style, x1=10)
    assert line.width == 7 and not hasattr(line, "fill")
    text = shape_from_style(TextShape, style, text="a")
    assert text.font_size == 50 and text.color == (1, 2, 3, 255)


def test_style_from_dict_is_tolerant():
    assert Style.from_dict(None) == Style()
    style = Style.from_dict({"color": [1, 2, 3], "width": "x", "font_family": "Comic", "fill": "nope"})
    assert style.color == (1, 2, 3, 255)
    assert style.width == Style().width
    assert style.font_family == fonts.SANS and style.fill is None
    assert Style.from_dict(Style(bold=True, fill=(1, 1, 1, 1)).to_dict()) == Style(bold=True, fill=(1, 1, 1, 1))
