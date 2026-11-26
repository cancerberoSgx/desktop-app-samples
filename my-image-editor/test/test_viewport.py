import pytest

from app.editor.viewport import FIT_MARGIN, Viewport, next_zoom_step, wheel_zoom_factor


def make(image=(1000, 500), view=(400, 300)) -> Viewport:
    vp = Viewport()
    vp.set_sizes(image, view)
    return vp


def test_round_trip_conversion():
    vp = make()
    vp.set_zoom(2.5, (100, 100))
    for point in ((0, 0), (123.5, 77.25)):
        assert vp.to_image(*vp.to_screen(*point)) == pytest.approx(point)


def test_zoom_keeps_anchor_point_fixed():
    vp = make()
    vp.set_zoom(2.0, (200, 150))
    before = vp.to_image(120, 80)
    vp.zoom_by(1.5, (120, 80))
    assert vp.to_image(120, 80) == pytest.approx(before)


def test_small_image_is_centred():
    vp = make(image=(100, 50), view=(400, 300))
    assert (vp.origin_x, vp.origin_y) == (150, 125)
    vp.pan(50, 50)                       # can't drag a fully visible image away
    assert (vp.origin_x, vp.origin_y) == (150, 125)


def test_pan_is_clamped_to_image_edges():
    vp = make(image=(1000, 1000), view=(400, 300))
    vp.pan(100, 100)
    assert (vp.origin_x, vp.origin_y) == (0, 0)
    vp.pan(-5000, -5000)
    assert (vp.origin_x, vp.origin_y) == (400 - 1000, 300 - 1000)


def test_fit_never_upscales_unless_asked():
    vp = make(image=(1000, 500), view=(400, 300))
    vp.fit()
    assert vp.zoom == pytest.approx((400 - 2 * FIT_MARGIN) / 1000)
    small = make(image=(50, 50), view=(400, 300))
    small.fit()
    assert small.zoom == 1.0
    small.fit(allow_upscale=True)
    assert small.zoom > 1.0


def test_zoom_steps_and_limits():
    assert next_zoom_step(1.0, +1) == 1.5
    assert next_zoom_step(1.0, -1) == pytest.approx(0.6667)
    assert next_zoom_step(1.2, +1) == 1.5 and next_zoom_step(1.2, -1) == 1.0
    assert next_zoom_step(64, +1) == 64
    vp = make()
    vp.set_zoom(1000)
    assert vp.zoom == 64


def test_wheel_factor():
    assert wheel_zoom_factor(120, 120) == pytest.approx(1.25)
    assert wheel_zoom_factor(-240, 120) == pytest.approx(1 / 1.5625)


def test_device_origin_scales_for_hidpi():
    vp = make(image=(100, 50), view=(400, 300))
    assert vp.device_origin(2.0) == (300, 250)
