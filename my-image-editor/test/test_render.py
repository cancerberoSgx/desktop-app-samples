from PIL import Image

from app.editor import fonts
from app.editor.render import ViewRenderer, checkerboard, composite, render_shape
from app.editor.shapes import ArrowShape, EllipseShape, LineShape, RectShape, TextShape
from conftest import BLUE, RED, WHITE, blank


def test_composite_draws_rect_stroke_and_fill():
    img = blank(100, 100)
    rect = RectShape(x0=20, y0=20, x1=80, y1=80, width=4, color=RED, fill=BLUE)
    out = composite(img, [rect])
    assert out.getpixel((20, 50)) == RED          # on the stroke (centred on the edge)
    assert out.getpixel((50, 50)) == BLUE         # fill
    assert out.getpixel((5, 5)) == WHITE
    assert img.getpixel((50, 50)) == WHITE        # source untouched


def test_shapes_are_clipped_to_the_image():
    out = composite(blank(50, 50), [LineShape(x0=-100, y0=25, x1=200, y1=25, width=6, color=RED)])
    assert out.size == (50, 50)
    assert out.getpixel((0, 25)) == RED and out.getpixel((49, 25)) == RED


def test_ellipse_and_arrow_draw_something():
    out = composite(blank(100, 100), [EllipseShape(x0=10, y0=10, x1=90, y1=90, width=4, color=RED)])
    assert out.getpixel((10, 50))[:3] != (255, 255, 255) and out.getpixel((50, 50)) == WHITE
    out = composite(blank(100, 40), [ArrowShape(x0=5, y0=20, x1=95, y1=20, width=4, color=RED)])
    assert out.getpixel((90, 20)) == RED          # inside the head
    assert out.getpixel((75, 13))[:3] != (255, 255, 255)  # head is wider than the line


def test_strokes_are_antialiased():
    out = composite(blank(100, 100), [LineShape(x0=10, y0=10, x1=90, y1=40, width=3, color=(0, 0, 0, 255))])
    greys = {out.getpixel((x, y))[0] for x in range(100) for y in range(100)}
    assert any(0 < g < 255 for g in greys)


def test_text_is_rendered_with_bundled_font():
    assert (fonts.fonts_dir() / "DejaVuSans.ttf").is_file()
    shape = TextShape(x=5, y=5, text="Hi", font_size=30, color=(0, 0, 0, 255))
    out = composite(blank(100, 50), [shape])
    x0, y0, x1, y1 = shape.bounds()
    assert out.crop((int(x0), int(y0), int(x1), int(y1))).getextrema()[0][0] == 0
    assert out.getpixel((90, 45)) == WHITE


def test_translucent_colour_blends():
    out = composite(blank(40, 40), [RectShape(x0=0, y0=0, x1=40, y1=40, width=1, fill=(255, 0, 0, 128))])
    r, g, b, a = out.getpixel((20, 20))
    assert r == 255 and 120 < g < 135 and a == 255


def test_render_shape_respects_clip_and_scale():
    rect = RectShape(x0=0, y0=0, x1=1000, y1=1000, width=2)
    tile, (left, top) = render_shape(rect, scale=8.0, clip=(100, 100, 300, 200))
    assert tile.size == (200, 100) and (left, top) == (100, 100)
    assert render_shape(rect, 1.0, clip=(5000, 5000, 5100, 5100)) is None


def test_view_renderer_zoomed_in_and_out():
    img = blank(100, 50)
    img.putpixel((10, 10), RED)
    vr = ViewRenderer()
    out, pos = vr.render(img, [], 4.0, (0, 0), (1000, 1000))
    assert out.size == (400, 200) and pos == (0, 0)
    assert out.getpixel((41, 41)) == RED[:3]      # pixel (10,10) is a 4x4 block
    out, pos = vr.render(img, [], 0.5, (20, 30), (1000, 1000))
    assert out.size == (50, 25) and pos == (20, 30)
    # Panned so only part is visible: clipped to the view.
    out, pos = vr.render(img, [], 2.0, (-50, -20), (60, 40))
    assert out.size == (60, 40) and pos == (0, 0)
    assert vr.render(img, [], 1.0, (500, 0), (100, 100)) is None


def test_view_renderer_draws_shapes_at_zoom():
    vr = ViewRenderer()
    rect = RectShape(x0=10, y0=10, x1=20, y1=20, width=2, color=RED, fill=BLUE)
    out, _ = vr.render(blank(100, 50), [rect], 2.0, (0, 0), (500, 500))
    assert out.getpixel((30, 30)) == BLUE[:3]
    assert out.getpixel((20, 30)) == RED[:3]


def test_view_renderer_checkerboard_only_for_transparent_images():
    vr = ViewRenderer()
    out, _ = vr.render(blank(32, 32, (0, 0, 0, 0)), [], 1.0, (0, 0), (32, 32))
    assert len(out.getcolors()) == 2
    out, _ = vr.render(blank(32, 32), [], 1.0, (0, 0), (32, 32))
    assert out.getcolors() == [(32 * 32, (255, 255, 255))]


def test_checkerboard_phase():
    a = checkerboard(32, 32, 0, 0)
    b = checkerboard(32, 32, 8, 0)
    assert a.getpixel((0, 0)) != b.getpixel((0, 0))
    assert a.getpixel((8, 0)) == b.getpixel((0, 0))
