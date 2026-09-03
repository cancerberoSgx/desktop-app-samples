import wx
import wx.adv

from .version import get_version

HOMEPAGE_URL = "https://github.com/cancerberoSgx/desktop-app-samples"


class AboutDialog(wx.Dialog):
    """Help > About - what this app does, and a link back to its home page.

    Replaces the old sidebar "About" page (see Sidebar.SIDEBAR_ITEMS) now
    that Exit/About have been pulled out of the sidebar in favor of the
    standard File/Help menu (see MainFrame._build_menu_bar)."""

    def __init__(self, parent: wx.Window, vector_enabled: bool) -> None:
        super().__init__(parent, title="About My Documents Viewer", size=(640, 660))

        outer = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="My Documents Viewer")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 8)
        font.MakeBold()
        title.SetFont(font)
        outer.Add(title, 0, wx.TOP | wx.LEFT | wx.RIGHT, 20)

        version = wx.StaticText(self, label=f"Version {get_version()}")
        version.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        outer.Add(version, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        subtitle = wx.StaticText(
            self, label="Index local text files and search them by keyword, meaning, or both."
        )
        outer.Add(subtitle, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        link = wx.adv.HyperlinkCtrl(self, label=HOMEPAGE_URL, url=HOMEPAGE_URL)
        outer.Add(link, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        body = wx.TextCtrl(self, value=_body_text(vector_enabled), style=wx.TE_MULTILINE | wx.TE_READONLY)
        outer.Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        close_btn = wx.Button(self, id=wx.ID_CLOSE, label="Close")
        close_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE))
        outer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 20)

        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)


def _body_text(vector_enabled: bool) -> str:
    vector_status = (
        "enabled (sqlite-vec loaded)"
        if vector_enabled
        else "unavailable on this build - falling back to full-text-only search"
    )
    return (
        "FEATURES\n"
        "\n"
        "Indexing\n"
        "  Add individual .txt/.md files or whole folders (indexed recursively).\n"
        "  Each file is split into overlapping chunks, hashed so unchanged files\n"
        "  are skipped on the next index, and organized into profiles - separate\n"
        "  document collections (e.g. \"History\", \"Development\", \"Contracts\"),\n"
        "  each with its own embedding backend and model.\n"
        "\n"
        "Full-text search\n"
        "  Keyword search over every indexed chunk via SQLite's FTS5 engine,\n"
        "  ranked by bm25 relevance.\n"
        "\n"
        "Semantic (vector) search\n"
        "  Chunks are embedded and compared by meaning rather than matching\n"
        f"  words, via sqlite-vec. Vector search: {vector_status}.\n"
        "\n"
        "Hybrid search\n"
        "  Full-text and vector rankings are blended with Reciprocal Rank\n"
        "  Fusion, so a result can rank highly for matching keywords, matching\n"
        "  meaning, or both - choose Hybrid, Full-text only, or Vector only per\n"
        "  search.\n"
        "\n"
        "Document viewer\n"
        "  Search results are grouped one row per document; opening one shows\n"
        "  its full text with every matching chunk highlighted and a table of\n"
        "  contents, sorted by relevance score, to jump straight to the best\n"
        "  passages.\n"
        "\n"
        "Chat with your docs (planned)\n"
        "  Asking questions in natural language and getting answers grounded in\n"
        "  your indexed documents is on the roadmap, built on top of this same\n"
        "  hybrid search index.\n"
        "\n"
        "Embedding backends\n"
        "  fastembed runs fully local with no API key. OpenAI and Gemini are\n"
        "  optional per-profile alternatives - add an API key on the Profiles\n"
        "  screen to use them.\n"
        "\n"
        "Data\n"
        "  Everything is stored locally in ~/.my-documents-viewer (SQLite,\n"
        "  schema managed via .sql migration files) - nothing leaves your\n"
        "  machine unless you opt into an API-key-based embedding backend.\n"
        "\n"
        "Built with wxPython (https://wxpython.org), fastembed\n"
        "(https://github.com/qdrant/fastembed) and sqlite-vec\n"
        "(https://github.com/asg017/sqlite-vec)."
    )
