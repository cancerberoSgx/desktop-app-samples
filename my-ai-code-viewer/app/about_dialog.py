import wx

from app.db.paths import db_path
from app.version import get_version


class AboutDialog(wx.Dialog):
    """Help > About."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title="About My AI Code Viewer")

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="My AI Code Viewer")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        version = wx.StaticText(self, label=f"Version {get_version()}")
        version.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        body = wx.StaticText(
            self,
            label=(
                "Browse every project and conversation you've had with AI coding\n"
                "CLIs - currently Claude Code, OpenCode and Codex.\n\n"
                "  - Pick a project folder, then a conversation, to read it\n"
                "    (tool calls and thinking collapsed by default).\n"
                "  - Conversations are archived into a local SQLite database, so\n"
                "    they stay readable after Claude Code prunes old transcripts\n"
                "    (~30 days by default).\n"
                "  - Source locations are configurable in File > Preferences.\n\n"
                "Built with wxPython (https://wxpython.org)."
            ),
        )

        body.Wrap(560)
        archive = wx.StaticText(self, label=f"Archive: {db_path()}", style=wx.ST_ELLIPSIZE_MIDDLE)
        archive.SetToolTip(str(db_path()))
        archive.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        sizer.Add(title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 24)
        sizer.Add(version, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(archive, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 24)
        sizer.Add(self.CreateSeparatedButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(sizer)
        self.Fit()
        self.SetSize((min(self.GetSize().width, 640), self.GetSize().height))
        self.CentreOnParent()
