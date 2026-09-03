import wx

from app.version import get_version


class AboutPage(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="My Redis Viewer")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        version = wx.StaticText(self, label=f"Version {get_version()}")
        version.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        body = wx.StaticText(
            self,
            label=(
                "Manage Redis connections, grouped by profile.\n\n"
                "Go to the Data Sources page to register a connection (host,\n"
                "port, user, password), then click Connect to PING the server\n"
                "and confirm it's reachable.\n\n"
                "  - Profiles and data sources are stored locally in\n"
                "    ~/.my-redis-viewer (SQLite, schema managed via .sql\n"
                "    migration files).\n"
                "  - Each profile has its own set of data sources - switch\n"
                "    profiles from the Profiles page.\n\n"
                "Built with wxPython (https://wxpython.org) and redis-py\n"
                "(https://github.com/redis/redis-py)."
            ),
        )

        sizer.Add(title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 24)
        sizer.Add(version, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)
