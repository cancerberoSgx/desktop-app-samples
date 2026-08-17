import sys

import wx

from app.frame import MainFrame


def main() -> None:
    app = wx.App(False)
    # An optional path (relative or absolute) to open on startup instead of
    # restoring the last-opened folder - `myfileviewer <path>`. A folder
    # opens directly; a file opens via its parent folder with the file
    # selected/scrolled into view - see MainFrame._restore_last_folder /
    # FolderExplorerPage.open_path.
    initial_path = sys.argv[1] if len(sys.argv) > 1 else None
    frame = MainFrame(initial_path=initial_path)
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
