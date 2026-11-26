"""Tool icons drawn in code (wx.GraphicsContext) - no image files to ship,
crisp at any DPI, and drawn in the theme's text colour so they work on light
and dark themes alike."""
import wx

ICON_SIZE = 20


def _new_bitmap(size: int, scale: float) -> wx.Bitmap:
    px = int(round(size * scale))
    bmp = wx.Bitmap.FromRGBA(px, px, 0, 0, 0, 0)
    if scale != 1 and hasattr(bmp, "SetScaleFactor"):
        bmp.SetScaleFactor(scale)
    return bmp


def tool_icon(name: str, colour: wx.Colour, scale: float = 1.0, size: int = ICON_SIZE) -> wx.Bitmap:
    bmp = _new_bitmap(size, scale)
    dc = wx.MemoryDC(bmp)
    gc = wx.GraphicsContext.Create(dc)
    if gc is None:
        dc.SelectObject(wx.NullBitmap)
        return bmp
    gc.SetAntialiasMode(wx.ANTIALIAS_DEFAULT)
    gc.Scale(scale * size / 20.0, scale * size / 20.0)  # draw on a 20x20 grid
    pen = gc.CreatePen(wx.GraphicsPenInfo(colour).Width(1.6).Cap(wx.CAP_ROUND).Join(wx.JOIN_ROUND))
    gc.SetPen(pen)
    gc.SetBrush(wx.TRANSPARENT_BRUSH)
    _DRAW.get(name, _draw_unknown)(gc, colour)
    del gc
    dc.SelectObject(wx.NullBitmap)
    return bmp


def _path(gc, points, close=False):
    path = gc.CreatePath()
    path.MoveToPoint(*points[0])
    for p in points[1:]:
        path.AddLineToPoint(*p)
    if close:
        path.CloseSubpath()
    return path


def _draw_select(gc, colour):
    path = _path(gc, [(5, 3), (5, 16), (8.5, 12.5), (11, 17.5), (13, 16.5), (10.5, 11.5), (15, 11.5)], close=True)
    gc.SetBrush(wx.Brush(colour))
    gc.FillPath(path)


def _draw_hand(gc, colour):
    # Four-way move arrows.
    gc.StrokePath(_path(gc, [(10, 2.5), (10, 17.5)]))
    gc.StrokePath(_path(gc, [(2.5, 10), (17.5, 10)]))
    for pts in ([(7.5, 5), (10, 2.5), (12.5, 5)], [(7.5, 15), (10, 17.5), (12.5, 15)],
                [(5, 7.5), (2.5, 10), (5, 12.5)], [(15, 7.5), (17.5, 10), (15, 12.5)]):
        gc.StrokePath(_path(gc, pts))


def _draw_crop(gc, colour):
    gc.StrokePath(_path(gc, [(5.5, 1.5), (5.5, 14.5), (18.5, 14.5)]))
    gc.StrokePath(_path(gc, [(1.5, 5.5), (14.5, 5.5), (14.5, 18.5)]))


def _draw_rect(gc, colour):
    gc.DrawRectangle(3, 5, 14, 10)


def _draw_ellipse(gc, colour):
    gc.DrawEllipse(2.5, 4.5, 15, 11)


def _draw_line(gc, colour):
    gc.StrokePath(_path(gc, [(3.5, 16.5), (16.5, 3.5)]))


def _draw_arrow(gc, colour):
    gc.StrokePath(_path(gc, [(3.5, 16.5), (14, 6)]))
    head = _path(gc, [(17, 3), (9.5, 5.5), (14.5, 10.5)], close=True)
    gc.SetBrush(wx.Brush(colour))
    gc.FillPath(head)


def _draw_text(gc, colour):
    gc.StrokePath(_path(gc, [(4, 5), (4, 3.5), (16, 3.5), (16, 5)]))
    gc.StrokePath(_path(gc, [(10, 3.5), (10, 16.5)]))
    gc.StrokePath(_path(gc, [(7.5, 16.5), (12.5, 16.5)]))


def _draw_unknown(gc, colour):
    gc.DrawEllipse(5, 5, 10, 10)


def _draw_collapse(gc, colour):
    gc.StrokePath(_path(gc, [(11, 5), (6, 10), (11, 15)]))
    gc.StrokePath(_path(gc, [(15, 5), (10, 10), (15, 15)]))


def _draw_expand(gc, colour):
    gc.StrokePath(_path(gc, [(5, 5), (10, 10), (5, 15)]))
    gc.StrokePath(_path(gc, [(9, 5), (14, 10), (9, 15)]))


_DRAW = {
    "select": _draw_select, "hand": _draw_hand, "crop": _draw_crop, "rect": _draw_rect,
    "ellipse": _draw_ellipse, "line": _draw_line, "arrow": _draw_arrow, "text": _draw_text,
    "collapse": _draw_collapse, "expand": _draw_expand,
}
