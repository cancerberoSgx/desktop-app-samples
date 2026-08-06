import sys

import wx

from app.frame import MainFrame


def main() -> None:
    app = wx.App(False)
    frame = MainFrame()
    frame.Show()
    # Optional: `python3 main.py /some/folder` opens straight into it,
    # skipping the Open Folder dialog - handy for a "open with" file
    # manager integration later, and for driving the app from a script.
    if len(sys.argv) > 1:
        frame.explorer_page.open_folder(sys.argv[1])
    app.MainLoop()


if __name__ == "__main__":
    main()
