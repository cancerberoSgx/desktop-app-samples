import wx


class AboutPage(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="My Docker Viewer")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        body = wx.StaticText(
            self,
            label=(
                "Admin your local Docker containers.\n\n"
                "Go to the Containers page to see every container (running\n"
                "and stopped), its status, image, size, and live CPU/memory\n"
                "usage, filter the list down, and stop or remove containers.\n\n"
                "  - This app has no Docker Engine API/SDK dependency - every\n"
                "    operation shells out to the 'docker' CLI, so it needs\n"
                "    Docker installed and reachable on this machine.\n"
                "  - App settings are stored locally in ~/.my-docker-viewer\n"
                "    (SQLite, schema managed via .sql migration files).\n\n"
                "Built with wxPython (https://wxpython.org)."
            ),
        )

        sizer.Add(title, 0, wx.ALL, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)
