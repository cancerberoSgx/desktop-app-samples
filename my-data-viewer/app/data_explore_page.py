import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import wx

from .models import ColumnInfo, Datasource, IndexInfo
from .repositories import DatasourceRepository


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class _Filter:
    column: str
    operator: str  # "=" or "LIKE"
    value: str


def _build_where(filters: List[_Filter]) -> Tuple[str, list]:
    if not filters:
        return "", []
    clauses = []
    params: list = []
    for f in filters:
        ident = _quote_ident(f.column)
        if f.operator == "LIKE":
            clauses.append(f"{ident} LIKE ?")
            params.append(f"%{f.value}%")
        else:
            clauses.append(f"{ident} = ?")
            params.append(f.value)
    return " WHERE " + " AND ".join(clauses), params


class PaginationBar(wx.Panel):
    """Page size / prev-next / current-page / total-records controls. Used
    twice per DataTab (top and bottom of the grid) - both instances are
    driven by the same callbacks so they always agree with each other."""

    PAGE_SIZES = ("25", "50", "100", "500")

    def __init__(
        self,
        parent: wx.Window,
        on_page_size_changed: Callable[[int], None],
        on_prev: Callable[[], None],
        on_next: Callable[[], None],
    ) -> None:
        super().__init__(parent)

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(wx.StaticText(self, label="Page size:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._page_size_choice = wx.Choice(self, choices=list(self.PAGE_SIZES))
        self._page_size_choice.SetSelection(1)
        sizer.Add(self._page_size_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        self._prev_btn = wx.Button(self, label="< Prev")
        sizer.Add(self._prev_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._page_label = wx.StaticText(self, label="Page 1 of 1")
        sizer.Add(self._page_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._next_btn = wx.Button(self, label="Next >")
        sizer.Add(self._next_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        self._total_label = wx.StaticText(self, label="0 records")
        sizer.Add(self._total_label, 0, wx.ALIGN_CENTER_VERTICAL)

        self.SetSizer(sizer)

        self._page_size_choice.Bind(
            wx.EVT_CHOICE,
            lambda evt: on_page_size_changed(int(self._page_size_choice.GetStringSelection())),
        )
        self._prev_btn.Bind(wx.EVT_BUTTON, lambda evt: on_prev())
        self._next_btn.Bind(wx.EVT_BUTTON, lambda evt: on_next())

    def update(self, page: int, page_size: int, total_records: int) -> None:
        total_pages = max(1, math.ceil(total_records / page_size)) if page_size else 1
        self._page_label.SetLabel(f"Page {page + 1} of {total_pages}")
        self._total_label.SetLabel(f"{total_records:,} records")
        self._prev_btn.Enable(page > 0)
        self._next_btn.Enable(page + 1 < total_pages)
        self._page_size_choice.SetStringSelection(str(page_size))


class _QueryResultListCtrl(wx.ListCtrl):
    """Virtual report-mode grid holding only the current page of rows -
    sorting/filtering/pagination are all pushed down into SQL (see DataTab),
    so however large the underlying table is, only one bounded page is ever
    fetched into memory."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_HRULES | wx.LC_VRULES | wx.BORDER_SUNKEN,
        )
        self.rows: List[tuple] = []

    def OnGetItemText(self, item: int, col: int) -> str:
        value = self.rows[item][col]
        return "" if value is None else str(value)

    def set_columns(self, columns: List[str]) -> None:
        while self.GetColumnCount() > 0:
            self.DeleteColumn(0)
        for i, name in enumerate(columns):
            self.InsertColumn(i, name)

    def set_rows(self, rows: List[tuple]) -> None:
        self.rows = rows
        self.SetItemCount(len(rows))
        self.Refresh()

    def autosize_columns(self, columns: List[str], rows: List[tuple]) -> None:
        for i, name in enumerate(columns):
            width = self.GetTextExtent(name)[0] + 24
            for row in rows:
                cell = row[i] if i < len(row) else None
                text = "" if cell is None else str(cell)
                width = max(width, self.GetTextExtent(text)[0] + 16)
            width = max(80, min(width, 400))
            self.SetColumnWidth(i, width)


class FieldsTab(wx.Panel):
    """Table fields: name, type and constraints."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Field", width=220)
        self._list.InsertColumn(1, "Type", width=120)
        self._list.InsertColumn(2, "Constraints", width=220)
        sizer.Add(self._list, 1, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(sizer)

    def load(self, columns: List[ColumnInfo]) -> None:
        self._list.DeleteAllItems()
        for row, col in enumerate(columns):
            self._list.InsertItem(row, col.name)
            self._list.SetItem(row, 1, col.type)
            self._list.SetItem(row, 2, col.constraints or "")

    def clear(self) -> None:
        self._list.DeleteAllItems()


class IndexesTab(wx.Panel):
    """Table indexes: name and the columns each one covers."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Index", width=220)
        self._list.InsertColumn(1, "Columns", width=320)
        sizer.Add(self._list, 1, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(sizer)

    def load(self, indexes: List[IndexInfo]) -> None:
        self._list.DeleteAllItems()
        for row, index in enumerate(indexes):
            self._list.InsertItem(row, index.name)
            self._list.SetItem(row, 1, ", ".join(index.columns))

    def clear(self) -> None:
        self._list.DeleteAllItems()


class DataTab(wx.Panel):
    """Table data view: `SELECT * FROM <table> WHERE ... ORDER BY ... LIMIT
    ... OFFSET ...` - sort (column header click), filter (per-column '='
    exact or LIKE contains) and pagination are all pushed down into that SQL,
    so only the current page of rows is ever fetched regardless of table
    size. Pagination controls are duplicated above and below the grid."""

    DEFAULT_PAGE_SIZE = 50

    def __init__(self, parent: wx.Window, repository: DatasourceRepository) -> None:
        super().__init__(parent)
        self._repository = repository
        self._datasource: Optional[Datasource] = None
        self._table: Optional[str] = None
        self._columns: List[str] = []
        self._filters: List[_Filter] = []
        self._sort_column: Optional[str] = None
        self._sort_ascending = True
        self._page = 0
        self._page_size = self.DEFAULT_PAGE_SIZE
        self._total_records = 0

        outer = wx.BoxSizer(wx.VERTICAL)

        filter_row = wx.BoxSizer(wx.HORIZONTAL)
        filter_row.Add(wx.StaticText(self, label="Column:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._filter_col_choice = wx.Choice(self)
        filter_row.Add(self._filter_col_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        filter_row.Add(wx.StaticText(self, label="Match:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._filter_op_choice = wx.Choice(self, choices=["= (exact)", "LIKE (contains)"])
        self._filter_op_choice.SetSelection(0)
        filter_row.Add(self._filter_op_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._filter_value_ctrl = wx.TextCtrl(self, size=(160, -1))
        filter_row.Add(self._filter_value_ctrl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._add_filter_btn = wx.Button(self, label="Add filter")
        filter_row.Add(self._add_filter_btn, 0, wx.RIGHT, 8)
        self._remove_filter_btn = wx.Button(self, label="Remove selected")
        filter_row.Add(self._remove_filter_btn, 0, wx.RIGHT, 8)
        self._clear_filters_btn = wx.Button(self, label="Clear filters")
        filter_row.Add(self._clear_filters_btn, 0)
        outer.Add(filter_row, 0, wx.EXPAND | wx.ALL, 8)

        self._filters_list = wx.ListBox(self, size=(-1, 50))
        outer.Add(self._filters_list, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._top_pagination = PaginationBar(self, self._on_page_size_changed, self._on_prev, self._on_next)
        outer.Add(self._top_pagination, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._grid = _QueryResultListCtrl(self)
        outer.Add(self._grid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._bottom_pagination = PaginationBar(self, self._on_page_size_changed, self._on_prev, self._on_next)
        outer.Add(self._bottom_pagination, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.SetSizer(outer)

        self._add_filter_btn.Bind(wx.EVT_BUTTON, self._on_add_filter)
        self._remove_filter_btn.Bind(wx.EVT_BUTTON, self._on_remove_filter)
        self._clear_filters_btn.Bind(wx.EVT_BUTTON, self._on_clear_filters)
        self._grid.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(self, datasource: Datasource, table: str) -> None:
        self._datasource = datasource
        self._table = table
        self._columns = [c.name for c in self._repository.list_columns(datasource, table)]

        self._filter_col_choice.Set(self._columns)
        if self._columns:
            self._filter_col_choice.SetSelection(0)
        self._filter_value_ctrl.SetValue("")
        self._filters = []
        self._filters_list.Set([])

        self._sort_column = None
        self._sort_ascending = True
        self._page = 0
        self._page_size = self.DEFAULT_PAGE_SIZE

        self._grid.set_columns(self._columns)
        self._reload_data()

    def clear(self) -> None:
        self._datasource = None
        self._table = None
        self._columns = []
        self._filters = []
        self._filters_list.Set([])
        self._filter_col_choice.Set([])
        self._grid.set_columns([])
        self._grid.set_rows([])
        self._top_pagination.update(0, self._page_size, 0)
        self._bottom_pagination.update(0, self._page_size, 0)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    def _reload_data(self) -> None:
        if self._datasource is None or self._table is None:
            return

        where_sql, where_params = _build_where(self._filters)
        table_ident = _quote_ident(self._table)

        count_result = self._repository.execute_sql(
            self._datasource, f"SELECT COUNT(*) FROM {table_ident}{where_sql}", where_params
        )
        self._total_records = int(count_result.rows[0][0]) if count_result.rows else 0

        total_pages = max(1, math.ceil(self._total_records / self._page_size))
        self._page = max(0, min(self._page, total_pages - 1))

        order_sql = ""
        if self._sort_column:
            direction = "ASC" if self._sort_ascending else "DESC"
            order_sql = f" ORDER BY {_quote_ident(self._sort_column)} {direction}"

        offset = self._page * self._page_size
        query = f"SELECT * FROM {table_ident}{where_sql}{order_sql} LIMIT ? OFFSET ?"
        result = self._repository.execute_sql(
            self._datasource, query, where_params + [self._page_size, offset]
        )

        self._grid.set_rows(result.rows)
        self._grid.autosize_columns(self._columns, result.rows)
        self._update_sort_indicators()
        self._top_pagination.update(self._page, self._page_size, self._total_records)
        self._bottom_pagination.update(self._page, self._page_size, self._total_records)

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    def _on_col_click(self, event: wx.ListEvent) -> None:
        col_index = event.GetColumn()
        if col_index < 0 or col_index >= len(self._columns):
            return
        column_name = self._columns[col_index]
        if column_name == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column_name
            self._sort_ascending = True
        self._page = 0
        self._reload_data()

    def _update_sort_indicators(self) -> None:
        for i, name in enumerate(self._columns):
            label = name
            if name == self._sort_column:
                label += " ▲" if self._sort_ascending else " ▼"
            item = self._grid.GetColumn(i)
            item.SetText(label)
            self._grid.SetColumn(i, item)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _on_add_filter(self, event: wx.CommandEvent) -> None:
        col_index = self._filter_col_choice.GetSelection()
        if col_index == wx.NOT_FOUND:
            return
        value = self._filter_value_ctrl.GetValue().strip()
        if not value:
            return
        column = self._columns[col_index]
        operator = "=" if self._filter_op_choice.GetSelection() == 0 else "LIKE"
        self._filters.append(_Filter(column, operator, value))
        self._filter_value_ctrl.SetValue("")
        self._refresh_filters_list()
        self._page = 0
        self._reload_data()

    def _on_remove_filter(self, event: wx.CommandEvent) -> None:
        index = self._filters_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        del self._filters[index]
        self._refresh_filters_list()
        self._page = 0
        self._reload_data()

    def _on_clear_filters(self, event: wx.CommandEvent) -> None:
        if not self._filters:
            return
        self._filters.clear()
        self._refresh_filters_list()
        self._page = 0
        self._reload_data()

    def _refresh_filters_list(self) -> None:
        self._filters_list.Set(
            [
                f"{f.column} = '{f.value}'" if f.operator == "=" else f"{f.column} LIKE '%{f.value}%'"
                for f in self._filters
            ]
        )

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def _on_page_size_changed(self, page_size: int) -> None:
        self._page_size = page_size
        self._page = 0
        self._reload_data()

    def _on_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._reload_data()

    def _on_next(self) -> None:
        self._page += 1
        self._reload_data()


class TableDetailPanel(wx.Panel):
    """Fields / Data / Indexes tabs for whichever table is selected."""

    def __init__(self, parent: wx.Window, repository: DatasourceRepository) -> None:
        super().__init__(parent)
        self._repository = repository

        notebook = wx.Notebook(self)
        self._fields_tab = FieldsTab(notebook)
        self._data_tab = DataTab(notebook, repository)
        self._indexes_tab = IndexesTab(notebook)
        notebook.AddPage(self._fields_tab, "Fields")
        notebook.AddPage(self._data_tab, "Data")
        notebook.AddPage(self._indexes_tab, "Indexes")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(notebook, 1, wx.EXPAND)
        self.SetSizer(sizer)

    def load_table(self, datasource: Datasource, table: str) -> None:
        self._fields_tab.load(self._repository.list_columns(datasource, table))
        self._indexes_tab.load(self._repository.list_indexes(datasource, table))
        self._data_tab.load(datasource, table)

    def clear(self) -> None:
        self._fields_tab.clear()
        self._indexes_tab.clear()
        self._data_tab.clear()


class DataExplorePage(wx.Panel):
    """Reached via "Connect" on the Datasources screen (not a sidebar
    destination): lists the connected datasource's tables on the left,
    and shows the selected table's Fields/Data/Indexes on the right."""

    def __init__(self, parent: wx.Window, repository: DatasourceRepository, on_back: Callable[[], None]) -> None:
        super().__init__(parent)
        self._repository = repository
        self._on_back = on_back
        self._datasource: Optional[Datasource] = None
        self._tables: List[str] = []

        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        self._back_btn = wx.Button(self, label="< Back to Datasources")
        header.Add(self._back_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self._title_label = wx.StaticText(self, label="Data Explore")
        font = self._title_label.GetFont()
        font.MakeBold()
        self._title_label.SetFont(font)
        header.Add(self._title_label, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(header, 0, wx.ALL, 12)

        body = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(self, label="Tables"), 0, wx.BOTTOM, 4)
        self._tables_list = wx.ListBox(self, choices=[], size=(220, -1))
        left.Add(self._tables_list, 1, wx.EXPAND)
        body.Add(left, 0, wx.EXPAND | wx.RIGHT, 12)

        self._detail = TableDetailPanel(self, repository)
        body.Add(self._detail, 1, wx.EXPAND)

        outer.Add(body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(outer)

        self._back_btn.Bind(wx.EVT_BUTTON, lambda evt: self._on_back())
        self._tables_list.Bind(wx.EVT_LISTBOX, self._on_table_selected)

    def load_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self._title_label.SetLabel(f"Data Explore — {datasource.name}")
        self._tables = self._repository.list_tables(datasource)
        self._tables_list.Set(self._tables)
        self._detail.clear()
        if self._tables:
            self._tables_list.SetSelection(0)
            self._detail.load_table(datasource, self._tables[0])

    def _on_table_selected(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        index = self._tables_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self._detail.load_table(self._datasource, self._tables[index])
