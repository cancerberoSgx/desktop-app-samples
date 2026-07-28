import wx


class AboutPage(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="My Data Viewer")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        body = wx.StaticText(
            self,
            label=(
                "Explore databases and CSV files through data sources you define.\n\n"
                "Go to the Datasources page to register a connection - for now,\n"
                "CSV files are supported: point at a .csv file and it becomes a\n"
                "queryable table, backed by DuckDB, so you can run SQL against it\n"
                "directly (sort, filter, aggregate...) without loading it all into\n"
                "memory yourself.\n\n"
                "  - Datasources are stored locally in ~/.my-data-viewer\n"
                "    (SQLite, schema managed via .sql migration files).\n"
                "  - Each datasource type is handled by a small driver that can\n"
                "    list tables/columns/indexes and run SQL against it.\n"
                "  - CSV datasources are queried with DuckDB.\n\n"
                "Built with wxPython (https://wxpython.org)."
            ),
        )

        sizer.Add(title, 0, wx.ALL, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)
