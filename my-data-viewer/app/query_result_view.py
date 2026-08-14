import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import wx
import wx.grid

from .drivers import CancelToken
from .models import Datasource, QueryResult
from .repositories import DatasourceRepository
from .task_manager import TaskManager


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
    twice per QueryResultPanel (top and bottom of the grid) - both instances
    are driven by the same callbacks so they always agree with each other."""

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


_COLUMN_HANDLE_GLYPH = "▤"


class _QueryResultTable(wx.grid.GridTableBase):
    """Virtual data source for `_QueryResultGrid` - holds only whatever rows
    the owner last handed it (see `_QueryResultGrid.set_rows`), same
    lazy/replace-in-place model the old `_QueryResultListCtrl.rows` used."""

    def __init__(self, columns: List[str], rows: List[tuple]) -> None:
        super().__init__()
        self.columns = columns
        self.rows = rows
        self.column_labels = list(columns)

    def GetNumberRows(self) -> int:
        return len(self.rows)

    def GetNumberCols(self) -> int:
        return len(self.columns)

    def IsEmptyCell(self, row: int, col: int) -> bool:
        return False

    def GetValue(self, row: int, col: int):
        value = self.rows[row][col]
        return "" if value is None else str(value)

    def SetValue(self, row: int, col: int, value) -> None:
        pass

    # `wx.grid.Grid.SetColLabelValue`/`GetColLabelValue` forward to these -
    # without an override the base class silently no-ops on Set and
    # GetColLabelValue falls back to spreadsheet-style "A"/"B"/"C" labels.
    def GetColLabelValue(self, col: int) -> str:
        return self.column_labels[col]

    def SetColLabelValue(self, col: int, value: str) -> None:
        self.column_labels[col] = value


class _QueryResultGrid(wx.grid.Grid):
    """Read-only Excel-like grid for query results: click selects a single
    cell, ctrl-click adds cells, shift-click extends a block (all native
    `wx.grid.Grid` behavior in the default GridSelectCells mode - no extra
    code needed), the row-number column on the left selects whole rows, and
    a thin glyph strip above each column name selects that whole column -
    clicking the name itself still sorts, same as before. Ctrl+C or the
    right-click menu copies the current selection as tab/newline-separated
    text."""

    def __init__(self, parent: wx.Window, on_sort_click: Optional[Callable[[int], None]] = None) -> None:
        super().__init__(parent)
        self._on_sort_click = on_sort_click
        self.SetTable(_QueryResultTable([], []), takeOwnership=True)
        self.EnableEditing(False)
        self.SetSelectionMode(wx.grid.Grid.GridSelectCells)
        self.DisableDragRowSize()

        dc = wx.ClientDC(self)
        dc.SetFont(self.GetLabelFont())
        line_height = dc.GetTextExtent("Xy")[1]
        self._handle_band_height = line_height + 4
        self.SetColLabelSize(2 * line_height + 12)

        self.Bind(wx.grid.EVT_GRID_LABEL_LEFT_CLICK, self._on_label_left_click)
        self.Bind(wx.grid.EVT_GRID_CELL_RIGHT_CLICK, self._on_cell_right_click)
        self.Bind(wx.grid.EVT_GRID_LABEL_RIGHT_CLICK, self._on_label_right_click)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def set_columns(self, columns: List[str]) -> None:
        self.SetTable(_QueryResultTable(columns, []), takeOwnership=True)
        self.set_header_labels(columns)

    def set_rows(self, rows: List[tuple]) -> None:
        table = self.GetTable()
        old_count = table.GetNumberRows()
        table.rows = rows
        new_count = len(rows)
        if new_count > old_count:
            msg = wx.grid.GridTableMessage(
                table, wx.grid.GRIDTABLE_NOTIFY_ROWS_APPENDED, new_count - old_count
            )
            self.ProcessTableMessage(msg)
        elif new_count < old_count:
            msg = wx.grid.GridTableMessage(
                table, wx.grid.GRIDTABLE_NOTIFY_ROWS_DELETED, 0, old_count - new_count
            )
            self.ProcessTableMessage(msg)
        self.ClearSelection()
        self.ForceRefresh()

    def set_header_labels(self, labels: List[str]) -> None:
        for i, label in enumerate(labels):
            self.SetColLabelValue(i, f"{_COLUMN_HANDLE_GLYPH}\n{label}")
        self.ForceRefresh()

    def autosize_columns(self, columns: List[str], rows: List[tuple]) -> None:
        # Only call this on column/schema changes (load), never from a
        # sort/filter reload - AutoSizeColumns() measures every cell with no
        # sampling, so re-running it per-keystroke on a large result set
        # would visibly lag.
        self.AutoSizeColumns(setAsMin=False)
        for i in range(len(columns)):
            self.SetColSize(i, max(80, min(self.GetColSize(i), 400)))

    # ------------------------------------------------------------------
    # Selection via headers
    # ------------------------------------------------------------------
    def _on_label_left_click(self, event: wx.grid.GridEvent) -> None:
        row, col = event.GetRow(), event.GetCol()
        if col == -1 and row >= 0:
            event.Skip()  # let native row selection (with ctrl/shift extend) happen
            return
        if row == -1 and col >= 0:
            if event.GetPosition().y <= self._handle_band_height:
                self.SelectCol(col, addToSelected=event.ControlDown())
            elif self._on_sort_click is not None:
                self._on_sort_click(col)
            return
        event.Skip()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------
    def _on_cell_right_click(self, event: wx.grid.GridEvent) -> None:
        row, col = event.GetRow(), event.GetCol()
        if not self.IsInSelection(row, col):
            self.SetGridCursor(row, col)
            self.SelectBlock(row, col, row, col)
        menu = wx.Menu()
        self.Bind(wx.EVT_MENU, lambda evt: self._copy_selection_to_clipboard(), menu.Append(wx.ID_ANY, "Copy"))
        self.Bind(wx.EVT_MENU, lambda evt: self.SelectRow(row), menu.Append(wx.ID_ANY, "Select Row"))
        self.Bind(wx.EVT_MENU, lambda evt: self.SelectCol(col), menu.Append(wx.ID_ANY, "Select Column"))
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_label_right_click(self, event: wx.grid.GridEvent) -> None:
        row, col = event.GetRow(), event.GetCol()
        if col == -1 and row >= 0:
            if not self.IsInSelection(row, 0):
                self.SelectRow(row)
        elif row == -1 and col >= 0:
            if not self.IsInSelection(0, col):
                self.SelectCol(col)
        else:
            return
        menu = wx.Menu()
        self.Bind(wx.EVT_MENU, lambda evt: self._copy_selection_to_clipboard(), menu.Append(wx.ID_ANY, "Copy"))
        self.PopupMenu(menu)
        menu.Destroy()

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------
    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.ControlDown() and event.GetKeyCode() == ord("C"):
            self._copy_selection_to_clipboard()
        else:
            event.Skip()

    def _copy_selection_to_clipboard(self) -> None:
        blocks = list(self.GetSelectedBlocks())
        if not blocks:
            row, col = self.GetGridCursorRow(), self.GetGridCursorCol()
            if row < 0 or col < 0:
                return
            blocks = [wx.grid.GridBlockCoords(row, col, row, col)]

        top = min(b.GetTopRow() for b in blocks)
        bottom = max(b.GetBottomRow() for b in blocks)
        left = min(b.GetLeftCol() for b in blocks)
        right = max(b.GetRightCol() for b in blocks)

        table = self.GetTable()
        lines = []
        for r in range(top, bottom + 1):
            cells = [table.GetValue(r, c) if self.IsInSelection(r, c) else "" for c in range(left, right + 1)]
            lines.append("\t".join(cells))
        text = "\n".join(lines)

        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()


class TableQuerySource:
    """`QueryResultPanel` source that pages through `SELECT * FROM <table>`
    with filter/sort/pagination pushed down into the SQL itself (`WHERE`/
    `ORDER BY`/`LIMIT`/`OFFSET`, via `DatasourceRepository.execute_sql`), so
    only the current page of rows is ever fetched regardless of table size.
    Used by `DataTab` to browse a table. Each `fetch()` runs two round-trips
    (a COUNT for the total, then the page itself) and is meant to be called
    off the UI thread - see `QueryResultPanel._reload_data`'s `is_async`
    branch."""

    is_async = True

    def __init__(self, repository: DatasourceRepository, datasource: Datasource, table: str) -> None:
        self._repository = repository
        self.datasource = datasource
        self.table = table

    def fetch(
        self,
        filters: List[_Filter],
        sort_column: Optional[str],
        sort_ascending: bool,
        page: int,
        page_size: int,
        cancel_token: Optional[CancelToken] = None,
    ) -> Tuple[int, int, QueryResult]:
        where_sql, where_params = _build_where(filters)
        table_ident = _quote_ident(self.table)

        count_result = self._repository.execute_sql(
            self.datasource, f"SELECT COUNT(*) FROM {table_ident}{where_sql}", where_params, cancel_token=cancel_token
        )
        total_records = int(count_result.rows[0][0]) if count_result.rows else 0

        total_pages = max(1, math.ceil(total_records / page_size))
        clamped_page = max(0, min(page, total_pages - 1))

        order_sql = ""
        if sort_column:
            direction = "ASC" if sort_ascending else "DESC"
            order_sql = f" ORDER BY {_quote_ident(sort_column)} {direction}"

        offset = clamped_page * page_size
        query = f"SELECT * FROM {table_ident}{where_sql}{order_sql} LIMIT ? OFFSET ?"
        result = self._repository.execute_sql(
            self.datasource, query, where_params + [page_size, offset], cancel_token=cancel_token
        )
        return total_records, clamped_page, result


class StaticQuerySource:
    """`QueryResultPanel` source that pages through an already-fetched
    `QueryResult`, filtering/sorting in Python - used for a script's result
    set, since the SQL behind it can be a join/aggregate/DDL statement the
    repository can't safely re-run with a pushed-down WHERE/ORDER BY/LIMIT
    the way a plain `SELECT * FROM <table>` can. `fetch()` is cheap (just
    list comprehensions over rows already in memory) so it's meant to be
    called synchronously on the UI thread - see `is_async`."""

    is_async = False

    def __init__(self, result: QueryResult) -> None:
        self.result = result

    def fetch(
        self,
        filters: List[_Filter],
        sort_column: Optional[str],
        sort_ascending: bool,
        page: int,
        page_size: int,
        cancel_token: Optional[CancelToken] = None,
    ) -> Tuple[int, int, QueryResult]:
        columns = self.result.columns
        rows = self.result.rows

        for f in filters:
            idx = columns.index(f.column)
            if f.operator == "LIKE":
                needle = f.value.lower()
                rows = [r for r in rows if needle in ("" if r[idx] is None else str(r[idx]).lower())]
            else:
                rows = [r for r in rows if ("" if r[idx] is None else str(r[idx])) == f.value]

        if sort_column is not None:
            idx = columns.index(sort_column)

            def key(row: tuple):
                value = row[idx]
                return (value is None, "" if value is None else value)

            try:
                rows = sorted(rows, key=key, reverse=not sort_ascending)
            except TypeError:
                # Mixed/uncomparable types in this column - fall back to string comparison.
                rows = sorted(
                    rows,
                    key=lambda r: (r[idx] is None, "" if r[idx] is None else str(r[idx])),
                    reverse=not sort_ascending,
                )

        total_records = len(rows)
        total_pages = max(1, math.ceil(total_records / page_size)) if page_size else 1
        clamped_page = max(0, min(page, total_pages - 1))
        offset = clamped_page * page_size
        page_rows = rows[offset : offset + page_size]
        return total_records, clamped_page, QueryResult(columns=columns, rows=page_rows)


class QueryResultPanel(wx.Panel):
    """Grid + filter + sort + pagination view for one page of a query
    result. Where the rows actually come from - a table paged live from the
    datasource, or an already-fetched script result filtered/sorted in
    Python - is supplied via `load()`'s `source` argument (a
    `TableQuerySource` or `StaticQuerySource`); from there on both behave
    identically: same filter UI, same grid, same pagination. This is the
    component both `DataTab` (table browsing) and `ScriptsTab` (a script's
    result set) render into - see those two `source` implementations above
    for what actually differs between the two."""

    DEFAULT_PAGE_SIZE = 50

    def __init__(self, parent: wx.Window, task_manager: Optional[TaskManager] = None) -> None:
        super().__init__(parent)
        self._task_manager = task_manager
        self._source = None
        self._on_first_result: Optional[Callable[[int, QueryResult], None]] = None
        self._error_title = "Load data"
        self._error_message = "Could not load data"
        self._task_label = "Querying"
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

        self._grid = _QueryResultGrid(self, on_sort_click=self._on_col_click)
        outer.Add(self._grid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._bottom_pagination = PaginationBar(self, self._on_page_size_changed, self._on_prev, self._on_next)
        outer.Add(self._bottom_pagination, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.SetSizer(outer)

        self._add_filter_btn.Bind(wx.EVT_BUTTON, self._on_add_filter)
        self._remove_filter_btn.Bind(wx.EVT_BUTTON, self._on_remove_filter)
        self._clear_filters_btn.Bind(wx.EVT_BUTTON, self._on_clear_filters)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(
        self,
        columns: List[str],
        source,
        *,
        initial: Optional[Tuple[int, QueryResult]] = None,
        autosize: bool = True,
        error_title: str = "Load data",
        error_message: str = "Could not load data",
        task_label: str = "Querying",
        on_first_result: Optional[Callable[[int, QueryResult], None]] = None,
    ) -> None:
        """Show `source`'s default view (page 0, no filter, no sort).
        `initial`, when given, is an already-fetched `(total_records,
        result)` pair for exactly that default view - rendered directly with
        no call to `source.fetch()` at all (used by `DataTab` when
        `TableMetadataCache` already has it). `on_first_result`, when given,
        fires exactly once - either immediately if `initial` was given, or
        after the first live fetch completes - so a caller can cache that
        first page without it re-firing on every later sort/filter/page
        change."""
        self._source = source
        self._error_title = error_title
        self._error_message = error_message
        self._task_label = task_label
        self._on_first_result = on_first_result
        self._columns = list(columns)
        self._filters = []
        self._filters_list.Set([])
        self._filter_col_choice.Set(self._columns)
        if self._columns:
            self._filter_col_choice.SetSelection(0)
        self._filter_value_ctrl.SetValue("")
        self._sort_column = None
        self._sort_ascending = True
        self._page = 0
        self._page_size = self.DEFAULT_PAGE_SIZE
        self._grid.set_columns(self._columns)

        if initial is not None:
            total_records, result = initial
            self._apply_data(total_records, result, autosize=autosize)
            if self._on_first_result is not None:
                self._on_first_result(total_records, result)
                self._on_first_result = None
        else:
            self._reload_data(autosize=autosize, first=True)

    def clear(self) -> None:
        self._source = None
        self._on_first_result = None
        self._columns = []
        self._filters = []
        self._filters_list.Set([])
        self._filter_col_choice.Set([])
        self._grid.set_columns([])
        self._grid.set_rows([])
        self._page = 0
        self._page_size = self.DEFAULT_PAGE_SIZE
        self._top_pagination.update(0, self._page_size, 0)
        self._bottom_pagination.update(0, self._page_size, 0)

    def _apply_data(self, total_records: int, result: QueryResult, autosize: bool = False) -> None:
        self._total_records = total_records
        self._grid.set_rows(result.rows)
        if autosize:
            self._grid.autosize_columns(self._columns, result.rows)
        self._update_sort_indicators()
        self._top_pagination.update(self._page, self._page_size, self._total_records)
        self._bottom_pagination.update(self._page, self._page_size, self._total_records)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    def _reload_data(self, autosize: bool = False, first: bool = False) -> None:
        """`first` is only ever True from `load()`'s own initial fetch - see
        `on_first_result` above; every sort/filter/pagination change re-fetches
        with `first=False` so it never re-triggers that callback."""
        if self._source is None:
            return

        source = self._source
        filters = list(self._filters)
        sort_column = self._sort_column
        sort_ascending = self._sort_ascending
        page = self._page
        page_size = self._page_size

        def on_success(payload: Tuple[int, int, QueryResult]) -> None:
            if source is not self._source:
                return  # a different source was loaded before this came back
            total_records, clamped_page, result = payload
            self._page = clamped_page
            self._apply_data(total_records, result, autosize=autosize)
            if first and self._on_first_result is not None:
                self._on_first_result(total_records, result)
                self._on_first_result = None

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"{self._error_message}:\n\n{exc}", self._error_title, wx.OK | wx.ICON_ERROR, self)

        if source.is_async:
            if self._task_manager is None:
                raise RuntimeError("QueryResultPanel needs a task_manager to load an async source")
            cancel_token = CancelToken()
            self._task_manager.start(
                label=self._task_label,
                work=lambda: source.fetch(
                    filters, sort_column, sort_ascending, page, page_size, cancel_token=cancel_token
                ),
                on_success=on_success,
                on_error=on_error,
                on_cancel_requested=cancel_token.cancel,
            )
        else:
            try:
                payload = source.fetch(filters, sort_column, sort_ascending, page, page_size)
            except Exception as exc:
                on_error(exc)
                return
            on_success(payload)

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    def _on_col_click(self, col_index: int) -> None:
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
        labels = []
        for name in self._columns:
            label = name
            if name == self._sort_column:
                label += " ▲" if self._sort_ascending else " ▼"
            labels.append(label)
        self._grid.set_header_labels(labels)

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
