import sys

import wx

from app import theme
from app.frame import MainFrame, read_setting_before_app


def main() -> None:
    # Native widgets can only be themed before the toolkit starts.
    override = theme.apply_native_theme(read_setting_before_app(theme.THEME_SETTING_KEY))
    app = wx.App(False)
    theme.forget_native_override(override)
    # `myimageeditor photo.jpg` opens it (also what "Open with" passes).
    frame = MainFrame(initial_path=sys.argv[1] if len(sys.argv) > 1 else None)
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
