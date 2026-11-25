import wx

from app.db.paths import db_path
from app.version import get_version


class AboutPage(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

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
                "CLIs - currently Claude Code and OpenCode.\n\n"
                "  - Projects page: pick a project folder, then a conversation,\n"
                "    to read it (tool calls and thinking collapsed by default).\n"
                "  - Conversations are archived into a local SQLite database, so\n"
                "    they stay readable after Claude Code prunes old transcripts\n"
                f"    (~30 days by default). Archive: {db_path()}\n"
                "  - Source locations are configurable on the Preferences page.\n\n"
                "Built with wxPython (https://wxpython.org)."
            ),
        )

        sizer.Add(title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 24)
        sizer.Add(version, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)
