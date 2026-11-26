import wx

from app.db.paths import db_path
from app.version import get_version


class AboutDialog(wx.Dialog):
    """Help > About."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title="About My Image Editor")

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="My Image Editor")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        version = wx.StaticText(self, label=f"Version {get_version()}")
        version.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        body = wx.StaticText(
            self,
            label=(
                "A lightweight image editor: open JPG / PNG images, crop and resize\n"
                "them, and annotate them with rectangles, ellipses, lines, arrows\n"
                "and text.\n\n"
                "  - Shapes and text stay editable (move, resize, restyle) until the\n"
                "    app is closed; saving writes them into the file.\n"
                "  - Undo / redo everything (Ctrl+Z / Ctrl+Shift+Z).\n"
                "  - Zoom with Ctrl+wheel, pan with Space+drag or the middle button.\n"
                "  - Text uses the bundled DejaVu fonts, so saved images look the same\n"
                "    on Linux, macOS and Windows.\n\n"
                "Built with wxPython (https://wxpython.org) and Pillow\n"
                "(https://python-pillow.org). DejaVu fonts: https://dejavu-fonts.github.io"
            ),
        )

        body.Wrap(560)
        settings = wx.StaticText(self, label=f"Settings: {db_path()}", style=wx.ST_ELLIPSIZE_MIDDLE)
        settings.SetToolTip(str(db_path()))
        settings.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        sizer.Add(title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 24)
        sizer.Add(version, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(settings, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 24)
        sizer.Add(self.CreateSeparatedButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(sizer)
        self.Fit()
        self.SetSize((min(self.GetSize().width, 640), self.GetSize().height))
        self.CentreOnParent()
