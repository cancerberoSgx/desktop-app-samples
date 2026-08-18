from typing import Callable, Optional

import wx


def bind_hover_path_tooltip(list_ctrl: wx.ListCtrl, get_path: Callable[[int], Optional[str]]) -> None:
    """Show a row's full path as a mouse-hover tooltip, regardless of what
    format the "File name display" setting has that row's cell rendered in
    (see file_display.format_display_path). `get_path(row)` returns the full
    path for that row index, or None/"" to show no tooltip.

    wx.ListCtrl has no built-in per-row tooltip API - this tracks which row
    the mouse last hovered via HitTest and swaps the whole control's tooltip
    text whenever that changes."""
    last_row = [wx.NOT_FOUND]

    def on_motion(event: wx.MouseEvent) -> None:
        row, _flags = list_ctrl.HitTest(event.GetPosition())
        if row != last_row[0]:
            last_row[0] = row
            path = get_path(row) if row != wx.NOT_FOUND else None
            list_ctrl.SetToolTip(path or None)
        event.Skip()

    list_ctrl.Bind(wx.EVT_MOTION, on_motion)
