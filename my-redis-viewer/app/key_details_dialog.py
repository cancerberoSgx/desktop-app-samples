import wx

from .async_task import AsyncTaskRunner
from .hash_table_ctrl import HashTableCtrl
from .json_tree_ctrl import JsonTreeCtrl
from .models import Datasource
from .repositories import DatasourceRepository

NO_EXPIRATION_TEXT = "No expiration"
LOADING_TEXT = "Loading..."

# Pages of the Value wx.Simplebook - which one is shown depends on the
# key's type (see _load's on_success).
_PAGE_PLAIN = 0
_PAGE_HASH = 1
_PAGE_JSON = 2


def _make_readonly_text(parent: wx.Window) -> wx.TextCtrl:
    text = wx.TextCtrl(parent, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.BORDER_SUNKEN)
    text.SetFont(wx.Font(wx.FontInfo().Family(wx.FONTFAMILY_TELETYPE)))
    return text


def _tabbed_panel(parent: wx.Window, pages) -> wx.Panel:
    """A Panel wrapping a Notebook with (label, page_window) tabs, sized to
    fill its parent - used for both the hash and json Value pages."""
    panel = wx.Panel(parent)
    notebook = wx.Notebook(panel)
    for label, page in pages(notebook):
        notebook.AddPage(page, label)
    sizer = wx.BoxSizer(wx.VERTICAL)
    sizer.Add(notebook, 1, wx.EXPAND)
    panel.SetSizer(sizer)
    return panel


class KeyDetailsDialog(wx.Dialog):
    """Shows everything known about a single Redis key: type, TTL,
    encoding, memory footprint, idle time, and its value. Most types
    render the value as a single read-only text block (binary values,
    e.g. vectors, fall back to a hex preview - see
    redis_value_format.format_bytes_as_text). Hash and RedisJSON
    ("ReJSON-RL") keys get a small Value sub-notebook instead - a
    structured view (Table / Json) as the default tab, plus a Value/Raw
    tab with the same flattened/pretty-printed text other types show.
    Self-contained so any screen can open it with just a repository, a
    datasource, and a key name - currently opened by double-clicking a
    key in the Data Explorer's tree view, but not tied to it."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        datasource: Datasource,
        key: str,
    ) -> None:
        super().__init__(
            parent,
            title=f"Key Details - {key}",
            size=(640, 520),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._repository = repository
        self._datasource = datasource
        self._key = key
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, gap=(8, 6))
        grid.AddGrowableCol(1)
        self._key_value = self._add_row(grid, "Key:")
        self._type_value = self._add_row(grid, "Type:")
        self._ttl_value = self._add_row(grid, "Expiration:")
        self._encoding_value = self._add_row(grid, "Encoding:")
        self._memory_value = self._add_row(grid, "Memory usage:")
        self._idle_value = self._add_row(grid, "Idle time:")
        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 12)

        outer.Add(wx.StaticText(self, label="Value:"), 0, wx.LEFT | wx.RIGHT, 12)
        self._value_book = wx.Simplebook(self)

        self._value_text = _make_readonly_text(self._value_book)
        self._value_book.AddPage(self._value_text, "value")

        def hash_pages(notebook):
            self._hash_table = HashTableCtrl(notebook)
            self._hash_value_text = _make_readonly_text(notebook)
            return [("Table", self._hash_table), ("Value", self._hash_value_text)]

        self._value_book.AddPage(_tabbed_panel(self._value_book, hash_pages), "hash")

        def json_pages(notebook):
            self._json_tree = JsonTreeCtrl(notebook)
            self._json_raw_text = _make_readonly_text(notebook)
            return [("Json", self._json_tree), ("Raw", self._json_raw_text)]

        self._value_book.AddPage(_tabbed_panel(self._value_book, json_pages), "json")

        outer.Add(self._value_book, 1, wx.EXPAND | wx.ALL, 12)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        close_btn = wx.Button(self, id=wx.ID_CLOSE, label="Close")
        button_sizer.AddStretchSpacer()
        button_sizer.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        button_sizer.Add(close_btn, 0)
        outer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(outer)

        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        close_btn.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))

        self._key_value.SetLabel(key)
        self._load()

    def _add_row(self, grid: wx.FlexGridSizer, label: str) -> wx.StaticText:
        grid.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_TOP)
        value = wx.StaticText(self, label=LOADING_TEXT)
        grid.Add(value, 0, wx.EXPAND)
        return value

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self._load()

    def _load(self) -> None:
        for value_widget in (
            self._type_value,
            self._ttl_value,
            self._encoding_value,
            self._memory_value,
            self._idle_value,
        ):
            value_widget.SetLabel(LOADING_TEXT)
        self._value_book.ChangeSelection(_PAGE_PLAIN)
        self._value_text.SetValue(LOADING_TEXT)

        def on_success(details) -> None:
            if not details.exists:
                self._type_value.SetLabel("(key not found)")
                self._ttl_value.SetLabel("")
                self._encoding_value.SetLabel("")
                self._memory_value.SetLabel("")
                self._idle_value.SetLabel("")
                self._value_book.ChangeSelection(_PAGE_PLAIN)
                self._value_text.SetValue("")
                return

            self._type_value.SetLabel(details.type)
            self._ttl_value.SetLabel(
                NO_EXPIRATION_TEXT if details.ttl_seconds is None else f"{details.ttl_seconds}s"
            )
            self._encoding_value.SetLabel(details.encoding or "?")
            self._memory_value.SetLabel(
                f"{details.memory_bytes:,} bytes" if details.memory_bytes is not None else "?"
            )
            self._idle_value.SetLabel(
                f"{details.idle_seconds}s" if details.idle_seconds is not None else "?"
            )

            value_text = details.value_text
            if details.value_truncated:
                value_text += "\n\n... (truncated - showing a limited number of entries)"

            if details.type == "hash":
                self._hash_table.set_fields(details.hash_fields or [])
                self._hash_value_text.SetValue(value_text)
                self._value_book.ChangeSelection(_PAGE_HASH)
            elif details.type == "ReJSON-RL":
                self._json_tree.set_value(details.json_value)
                self._json_raw_text.SetValue(value_text)
                self._value_book.ChangeSelection(_PAGE_JSON)
            else:
                self._value_text.SetValue(value_text)
                self._value_book.ChangeSelection(_PAGE_PLAIN)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not load details for "{self._key}":\n\n{exc}',
                "Key details failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.get_key_details(self._datasource, self._key),
            on_success=on_success,
            on_error=on_error,
            disable=[self._refresh_btn],
        )
