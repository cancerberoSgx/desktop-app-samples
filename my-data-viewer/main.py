import wx

from app.frame import MainFrame


def main():
    app = wx.App(False)
    frame = MainFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
