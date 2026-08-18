import wx


class AboutPage(wx.Panel):
    def __init__(self, parent: wx.Window, vector_enabled: bool) -> None:
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="My Documents Viewer")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)

        vector_status = (
            "enabled (sqlite-vec loaded)"
            if vector_enabled
            else "unavailable on this build - falling back to full-text-only search"
        )

        body = wx.StaticText(
            self,
            label=(
                "Index local text files (.txt, .md) for full-text and semantic\n"
                "similarity search, then find the most relevant passages across\n"
                "everything you've indexed.\n\n"
                "  - Profiles group documents by kind (e.g. \"History\",\n"
                "    \"Development\", \"Contracts\") - each profile picks its own\n"
                "    embedding model and dimension.\n"
                "  - Documents indexes .txt/.md files or whole folders. Text is\n"
                "    split into overlapping chunks, embedded, and stored for\n"
                "    both keyword (FTS5) and vector (sqlite-vec) search.\n"
                "  - Search runs both and blends the rankings (Reciprocal Rank\n"
                "    Fusion), or either alone.\n\n"
                f"Vector search: {vector_status}\n\n"
                "  - Data is stored locally in ~/.my-documents-viewer (SQLite,\n"
                "    schema managed via .sql migration files).\n"
                "  - Embeddings default to fastembed (local, no API key); OpenAI\n"
                "    and Gemini are optional per-profile alternatives - add an\n"
                "    API key on the Profiles screen to use them.\n\n"
                "Built with wxPython (https://wxpython.org), fastembed\n"
                "(https://github.com/qdrant/fastembed) and sqlite-vec\n"
                "(https://github.com/asg017/sqlite-vec)."
            ),
        )

        sizer.Add(title, 0, wx.ALL, 24)
        sizer.Add(body, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 24)
        self.SetSizer(sizer)
