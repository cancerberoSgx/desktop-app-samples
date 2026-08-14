import wx

from .async_task import AsyncTaskRunner
from .models import Datasource
from .repositories import DatasourceRepository

LOADING_TEXT = "Loading..."


class IndexDetailsDialog(wx.Dialog):
    """Shows everything FT.INFO reports about a single RediSearch index:
    key type, key prefixes, doc/record counts, indexing status, and every
    field the index was created with (identifier, alias, type, flags,
    extra attributes like WEIGHT). Self-contained so any screen can open
    it with just a repository, a datasource, and an index name - currently
    opened by activating an index in the Data Explorer's Indexes tab, but
    not tied to it (mirrors KeyDetailsDialog)."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        datasource: Datasource,
        index_name: str,
    ) -> None:
        super().__init__(
            parent,
            title=f"Index Details - {index_name}",
            size=(760, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._repository = repository
        self._datasource = datasource
        self._index_name = index_name
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=4, gap=(8, 6))
        grid.AddGrowableCol(1)
        grid.AddGrowableCol(3)
        self._name_value = self._add_row(grid, "Name:")
        self._key_type_value = self._add_row(grid, "Key type:")
        self._prefixes_value = self._add_row(grid, "Prefixes:")
        self._docs_value = self._add_row(grid, "Docs:")
        self._records_value = self._add_row(grid, "Records:")
        self._indexing_value = self._add_row(grid, "Indexing:")
        self._percent_value = self._add_row(grid, "% indexed:")
        self._failures_value = self._add_row(grid, "Hash indexing failures:")
        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 12)

        outer.Add(wx.StaticText(self, label="Fields:"), 0, wx.LEFT | wx.RIGHT, 12)
        self._fields_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self._fields_list.InsertColumn(0, "Identifier", width=160)
        self._fields_list.InsertColumn(1, "Attribute (alias)", width=140)
        self._fields_list.InsertColumn(2, "Type", width=90)
        self._fields_list.InsertColumn(3, "Flags", width=160)
        self._fields_list.InsertColumn(4, "Extra", width=140)
        outer.Add(self._fields_list, 1, wx.EXPAND | wx.ALL, 12)

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

        self._name_value.SetLabel(index_name)
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
            self._key_type_value,
            self._prefixes_value,
            self._docs_value,
            self._records_value,
            self._indexing_value,
            self._percent_value,
            self._failures_value,
        ):
            value_widget.SetLabel(LOADING_TEXT)
        self._fields_list.DeleteAllItems()

        def on_success(details) -> None:
            self._key_type_value.SetLabel(details.key_type or "?")
            self._prefixes_value.SetLabel(", ".join(details.prefixes) or "(none)")
            self._docs_value.SetLabel(f"{details.num_docs:,}")
            self._records_value.SetLabel(f"{details.num_records:,}")
            self._indexing_value.SetLabel("Yes" if details.indexing else "No")
            self._percent_value.SetLabel(
                "?" if details.percent_indexed is None else f"{details.percent_indexed * 100:.1f}%"
            )
            self._failures_value.SetLabel(f"{details.hash_indexing_failures:,}")

            self._fields_list.DeleteAllItems()
            for row, field in enumerate(details.fields):
                self._fields_list.InsertItem(row, field.identifier)
                self._fields_list.SetItem(row, 1, field.attribute)
                self._fields_list.SetItem(row, 2, field.type)
                self._fields_list.SetItem(row, 3, ", ".join(field.flags))
                self._fields_list.SetItem(
                    row, 4, ", ".join(f"{k}={v}" for k, v in field.extra.items())
                )

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not load details for index "{self._index_name}":\n\n{exc}',
                "Index details failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.get_index_details(self._datasource, self._index_name),
            on_success=on_success,
            on_error=on_error,
            disable=[self._refresh_btn],
        )
