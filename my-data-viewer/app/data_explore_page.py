import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import wx
import wx.stc as stc

from .models import ColumnInfo, Datasource, IndexInfo, QueryResult, Script
from .repositories import DatasourceRepository, ScriptRepository


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


def _split_sql_statements(script: str) -> List[str]:
    """Split a script into individual statements on top-level ';' characters
    - quote-aware (a ';' inside a '...' or "..." string doesn't end the
    statement early) but not a full SQL parser, which is enough for the
    "run whole script" case since each statement is then executed on its
    own via execute_sql."""
    statements = []
    current: List[str] = []
    quote_char = None
    for ch in script:
        if quote_char:
            current.append(ch)
            if ch == quote_char:
                quote_char = None
            continue
        if ch in ("'", '"'):
            quote_char = ch
            current.append(ch)
            continue
        if ch == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


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


class SqlEditor(stc.StyledTextCtrl):
    """A textarea with SQL syntax highlighting, via Scintilla's built-in SQL
    lexer (no external dependency - wx.stc ships with wxPython)."""

    _KEYWORDS = (
        "select insert update delete from where join inner outer left right full "
        "on group by having order limit offset as into values set create table "
        "drop alter add column primary key foreign references not null default "
        "and or in is like between exists union all distinct case when then else "
        "end asc desc with view index using cast"
    )

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetLexer(stc.STC_LEX_SQL)
        self.SetKeyWords(0, self._KEYWORDS)

        font = wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.StyleSetFont(stc.STC_STYLE_DEFAULT, font)
        self.StyleClearAll()
        self.StyleSetForeground(stc.STC_SQL_COMMENT, wx.Colour(110, 110, 110))
        self.StyleSetForeground(stc.STC_SQL_COMMENTLINE, wx.Colour(110, 110, 110))
        self.StyleSetForeground(stc.STC_SQL_COMMENTDOC, wx.Colour(110, 110, 110))
        self.StyleSetForeground(stc.STC_SQL_NUMBER, wx.Colour(0, 128, 128))
        self.StyleSetForeground(stc.STC_SQL_STRING, wx.Colour(163, 21, 21))
        self.StyleSetForeground(stc.STC_SQL_CHARACTER, wx.Colour(163, 21, 21))
        self.StyleSetForeground(stc.STC_SQL_WORD, wx.Colour(0, 0, 200))
        self.StyleSetBold(stc.STC_SQL_WORD, True)
        self.StyleSetForeground(stc.STC_SQL_OPERATOR, wx.Colour(80, 0, 80))

        self.SetMarginType(1, stc.STC_MARGIN_NUMBER)
        self.SetMarginWidth(1, 32)
        self.SetTabWidth(4)
        self.SetUseTabs(False)

    def clear_and_disable(self) -> None:
        self.SetReadOnly(False)
        self.SetText("")
        self.EmptyUndoBuffer()
        self.SetSavePoint()
        self.Enable(False)

    def load_content(self, content: str) -> None:
        self.Enable(True)
        self.SetReadOnly(False)
        self.SetText(content)
        self.EmptyUndoBuffer()
        self.SetSavePoint()


class ScriptResultPanel(wx.Panel):
    """Displays a QueryResult with client-side sort (column header click)
    and filter (per-column '=' exact or LIKE contains) - unlike DataTab,
    filtering/sorting here runs in Python over the already-fetched rows,
    since a script's output can be arbitrary SQL (joins, aggregates, DDL...)
    rather than a plain `SELECT * FROM <table>` the repository could re-run
    with a pushed-down WHERE/ORDER BY/LIMIT."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._columns: List[str] = []
        self._all_rows: List[tuple] = []
        self._filters: List[_Filter] = []
        self._sort_column: Optional[int] = None
        self._sort_ascending = True

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
        outer.Add(filter_row, 0, wx.EXPAND | wx.BOTTOM, 8)

        self._filters_list = wx.ListBox(self, size=(-1, 50))
        outer.Add(self._filters_list, 0, wx.EXPAND | wx.BOTTOM, 8)

        self._grid = _QueryResultListCtrl(self)
        outer.Add(self._grid, 1, wx.EXPAND)

        self._status_label = wx.StaticText(self, label="")
        outer.Add(self._status_label, 0, wx.TOP, 4)

        self.SetSizer(outer)

        self._add_filter_btn.Bind(wx.EVT_BUTTON, self._on_add_filter)
        self._remove_filter_btn.Bind(wx.EVT_BUTTON, self._on_remove_filter)
        self._clear_filters_btn.Bind(wx.EVT_BUTTON, self._on_clear_filters)
        self._grid.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)

    def load(self, result: QueryResult) -> None:
        self._columns = result.columns
        self._all_rows = result.rows
        self._filters = []
        self._filters_list.Set([])
        self._sort_column = None
        self._sort_ascending = True
        self._filter_col_choice.Set(self._columns)
        if self._columns:
            self._filter_col_choice.SetSelection(0)
        self._filter_value_ctrl.SetValue("")
        self._grid.set_columns(self._columns)
        self._apply()

    def clear(self) -> None:
        self.load(QueryResult(columns=[], rows=[]))

    def _apply(self) -> None:
        rows = self._all_rows
        for f in self._filters:
            idx = self._columns.index(f.column)
            if f.operator == "LIKE":
                needle = f.value.lower()
                rows = [r for r in rows if needle in ("" if r[idx] is None else str(r[idx]).lower())]
            else:
                rows = [r for r in rows if ("" if r[idx] is None else str(r[idx])) == f.value]

        if self._sort_column is not None:
            idx = self._sort_column

            def key(row: tuple):
                value = row[idx]
                return (value is None, "" if value is None else value)

            try:
                rows = sorted(rows, key=key, reverse=not self._sort_ascending)
            except TypeError:
                # Mixed/uncomparable types in this column - fall back to string comparison.
                rows = sorted(
                    rows,
                    key=lambda r: (r[idx] is None, "" if r[idx] is None else str(r[idx])),
                    reverse=not self._sort_ascending,
                )

        self._grid.set_rows(rows)
        self._grid.autosize_columns(self._columns, rows)
        self._update_sort_indicators()
        self._status_label.SetLabel(f"{len(rows):,} of {len(self._all_rows):,} rows")

    def _on_col_click(self, event: wx.ListEvent) -> None:
        col_index = event.GetColumn()
        if col_index < 0 or col_index >= len(self._columns):
            return
        if col_index == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = col_index
            self._sort_ascending = True
        self._apply()

    def _update_sort_indicators(self) -> None:
        for i, name in enumerate(self._columns):
            label = name
            if i == self._sort_column:
                label += " ▲" if self._sort_ascending else " ▼"
            item = self._grid.GetColumn(i)
            item.SetText(label)
            self._grid.SetColumn(i, item)

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
        self._apply()

    def _on_remove_filter(self, event: wx.CommandEvent) -> None:
        index = self._filters_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        del self._filters[index]
        self._refresh_filters_list()
        self._apply()

    def _on_clear_filters(self, event: wx.CommandEvent) -> None:
        if not self._filters:
            return
        self._filters.clear()
        self._refresh_filters_list()
        self._apply()

    def _refresh_filters_list(self) -> None:
        self._filters_list.Set(
            [
                f"{f.column} = '{f.value}'" if f.operator == "=" else f"{f.column} LIKE '%{f.value}%'"
                for f in self._filters
            ]
        )


class ScriptsTab(wx.Panel):
    """Scripts belonging to the current datasource: list/create/rename/
    delete saved SQL scripts, edit their content in a syntax-highlighted
    editor, and run either the whole script or just the selected statement
    against the datasource - results render in ScriptResultPanel (same
    list-ctrl grid as the Data tab, sortable/filterable client-side)."""

    def __init__(
        self, parent: wx.Window, script_repository: ScriptRepository, datasource_repository: DatasourceRepository
    ) -> None:
        super().__init__(parent)
        self._script_repository = script_repository
        self._datasource_repository = datasource_repository
        self._datasource: Optional[Datasource] = None
        self._scripts: List[Script] = []
        self._current_script: Optional[Script] = None

        outer = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(self, label="Scripts"), 0, wx.BOTTOM, 4)
        self._list = wx.ListBox(self, choices=[], size=(200, -1))
        left.Add(self._list, 1, wx.EXPAND | wx.BOTTOM, 8)
        list_btns = wx.BoxSizer(wx.HORIZONTAL)
        self._new_btn = wx.Button(self, label="New")
        self._rename_btn = wx.Button(self, label="Rename")
        self._delete_btn = wx.Button(self, label="Delete")
        list_btns.Add(self._new_btn, 0, wx.RIGHT, 4)
        list_btns.Add(self._rename_btn, 0, wx.RIGHT, 4)
        list_btns.Add(self._delete_btn, 0)
        left.Add(list_btns, 0)
        outer.Add(left, 0, wx.EXPAND | wx.ALL, 12)

        right = wx.BoxSizer(wx.VERTICAL)
        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._save_btn = wx.Button(self, label="Save")
        self._run_all_btn = wx.Button(self, label="Run All")
        self._run_selected_btn = wx.Button(self, label="Run Selected")
        toolbar.Add(self._save_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._run_all_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._run_selected_btn, 0)
        right.Add(toolbar, 0, wx.EXPAND | wx.BOTTOM, 8)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self._editor = SqlEditor(splitter)
        self._result_panel = ScriptResultPanel(splitter)
        splitter.SplitHorizontally(self._editor, self._result_panel)
        splitter.SetSashGravity(0.4)
        splitter.SetMinimumPaneSize(80)
        right.Add(splitter, 1, wx.EXPAND)

        outer.Add(right, 1, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(outer)

        self._list.Bind(wx.EVT_LISTBOX, self._on_script_selected)
        self._new_btn.Bind(wx.EVT_BUTTON, self._on_new)
        self._rename_btn.Bind(wx.EVT_BUTTON, self._on_rename)
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        self._run_all_btn.Bind(wx.EVT_BUTTON, self._on_run_all)
        self._run_selected_btn.Bind(wx.EVT_BUTTON, self._on_run_selected)

        self._editor.clear_and_disable()
        self._update_button_states()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_datasource(self, datasource: Datasource) -> None:
        self._save_current()
        self._datasource = datasource
        self._reload_scripts(select_id=None)

    def clear(self) -> None:
        self._save_current()
        self._datasource = None
        self._scripts = []
        self._current_script = None
        self._list.Set([])
        self._editor.clear_and_disable()
        self._result_panel.clear()
        self._update_button_states()

    def _reload_scripts(self, select_id: Optional[int]) -> None:
        self._scripts = self._script_repository.list(self._datasource.id) if self._datasource else []
        self._list.Set([s.name for s in self._scripts])
        index = wx.NOT_FOUND
        if select_id is not None:
            index = next((i for i, s in enumerate(self._scripts) if s.id == select_id), wx.NOT_FOUND)
        elif self._scripts:
            index = 0
        if index != wx.NOT_FOUND:
            self._list.SetSelection(index)
        self._load_selected()

    def _selected_script(self) -> Optional[Script]:
        index = self._list.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self._scripts[index]

    def _load_selected(self) -> None:
        script = self._selected_script()
        self._current_script = script
        if script is None:
            self._editor.clear_and_disable()
        else:
            self._editor.load_content(script.content)
        self._result_panel.clear()
        self._update_button_states()

    def _update_button_states(self) -> None:
        has_datasource = self._datasource is not None
        has_selection = self._current_script is not None
        self._new_btn.Enable(has_datasource)
        self._rename_btn.Enable(has_selection)
        self._delete_btn.Enable(has_selection)
        self._save_btn.Enable(has_selection)
        self._run_all_btn.Enable(has_selection)
        self._run_selected_btn.Enable(has_selection)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save_current(self) -> None:
        if self._current_script is None or not self._editor.GetModify():
            return
        self._current_script.content = self._editor.GetText()
        self._script_repository.update(self._current_script)
        self._editor.SetSavePoint()

    def _on_save(self, event: wx.CommandEvent) -> None:
        self._save_current()

    def _on_script_selected(self, event: wx.CommandEvent) -> None:
        self._save_current()
        self._load_selected()

    # ------------------------------------------------------------------
    # Create / rename / delete
    # ------------------------------------------------------------------
    def _prompt_name(self, title: str, message: str, initial: str) -> Optional[str]:
        dlg = wx.TextEntryDialog(self, message, title, value=initial)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return None
            return dlg.GetValue().strip()
        finally:
            dlg.Destroy()

    def _on_new(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        name = self._prompt_name("New script", "Script name:", "")
        if not name:
            return
        self._save_current()
        script = Script(
            id=None,
            name=name,
            content="",
            profile_id=self._datasource.profile_id,
            datasource_id=self._datasource.id,
        )
        created = self._script_repository.create(script)
        self._reload_scripts(select_id=created.id)

    def _on_rename(self, event: wx.CommandEvent) -> None:
        script = self._selected_script()
        if script is None:
            return
        name = self._prompt_name("Rename script", "Script name:", script.name)
        if not name or name == script.name:
            return
        script.name = name
        self._script_repository.update(script)
        self._reload_scripts(select_id=script.id)

    def _on_delete(self, event: wx.CommandEvent) -> None:
        script = self._selected_script()
        if script is None:
            return
        confirm = wx.MessageBox(
            f'Delete script "{script.name}"?', "Confirm delete", wx.YES_NO | wx.ICON_WARNING, self
        )
        if confirm != wx.YES:
            return
        self._script_repository.delete(script.id)
        if self._current_script is not None and self._current_script.id == script.id:
            self._current_script = None
        self._reload_scripts(select_id=None)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def _on_run_all(self, event: wx.CommandEvent) -> None:
        self._execute(self._editor.GetText())

    def _on_run_selected(self, event: wx.CommandEvent) -> None:
        selected = self._editor.GetSelectedText().strip()
        if not selected:
            wx.MessageBox(
                "Select a single statement in the editor first.", "Run Selected", wx.OK | wx.ICON_WARNING, self
            )
            return
        self._execute(selected)

    def _execute(self, script_text: str) -> None:
        if self._datasource is None:
            return
        statements = _split_sql_statements(script_text)
        if not statements:
            wx.MessageBox("Nothing to run.", "Run", wx.OK | wx.ICON_WARNING, self)
            return
        try:
            result = None
            for statement in statements:
                result = self._datasource_repository.execute_sql(self._datasource, statement)
        except Exception as exc:
            wx.MessageBox(f"Query failed:\n\n{exc}", "Execution error", wx.OK | wx.ICON_ERROR, self)
            return
        self._result_panel.load(result)


class DataExplorePage(wx.Panel):
    """Reached via "Connect" on the Datasources screen (not a sidebar
    destination): a "Tables" tab lists the connected datasource's tables on
    the left and shows the selected table's Fields/Data/Indexes on the
    right, and a "Scripts" tab manages saved SQL scripts for it."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        script_repository: ScriptRepository,
        on_back: Callable[[], None],
    ) -> None:
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

        notebook = wx.Notebook(self)

        tables_panel = wx.Panel(notebook)
        body = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(tables_panel, label="Tables"), 0, wx.BOTTOM, 4)
        self._tables_list = wx.ListBox(tables_panel, choices=[], size=(220, -1))
        left.Add(self._tables_list, 1, wx.EXPAND)
        body.Add(left, 0, wx.EXPAND | wx.RIGHT, 12)

        self._detail = TableDetailPanel(tables_panel, repository)
        body.Add(self._detail, 1, wx.EXPAND)

        tables_outer = wx.BoxSizer(wx.VERTICAL)
        tables_outer.Add(body, 1, wx.EXPAND | wx.ALL, 12)
        tables_panel.SetSizer(tables_outer)
        notebook.AddPage(tables_panel, "Tables")

        self._scripts_tab = ScriptsTab(notebook, script_repository, repository)
        notebook.AddPage(self._scripts_tab, "Scripts")

        outer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(outer)

        self._back_btn.Bind(wx.EVT_BUTTON, lambda evt: self._on_back())
        self._tables_list.Bind(wx.EVT_LISTBOX, self._on_table_selected)

    def load_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self._title_label.SetLabel(f"Data Explore — {datasource.name}")
        try:
            self._tables = self._repository.list_tables(datasource)
        except Exception as exc:
            wx.MessageBox(
                f'Could not list tables for "{datasource.name}":\n\n{exc}',
                "Load failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self._tables = []
        self._tables_list.Set(self._tables)
        self._detail.clear()
        if self._tables:
            self._tables_list.SetSelection(0)
            self._load_table(datasource, self._tables[0])
        self._scripts_tab.load_datasource(datasource)

    def _on_table_selected(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        index = self._tables_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self._load_table(self._datasource, self._tables[index])

    def _load_table(self, datasource: Datasource, table: str) -> None:
        try:
            self._detail.load_table(datasource, table)
        except Exception as exc:
            wx.MessageBox(
                f'Could not load table "{table}":\n\n{exc}',
                "Load failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )
