from typing import Callable, Dict, List, Optional, Tuple

import wx
import wx.stc as stc

from .drivers import CancelToken
from .models import ColumnInfo, Datasource, IndexInfo, QueryResult, Script
from .query_result_view import QueryResultPanel, StaticQuerySource, TableQuerySource
from .repositories import DatasourceRepository, ScriptRepository
from .task_manager import TaskManager


class TableMetadataCache:
    """In-memory cache of a datasource's table list, each table's
    columns/indexes, and each table's *default* Data-tab view (page 0, no
    filter, no sort - exactly what selecting a table first shows). Selecting
    a table used to always run list_columns + list_indexes + a COUNT/SELECT
    in serial, even for a table already looked at this session; each of
    those is a real driver round-trip (throttled with a deliberate sleep(2)
    for csv/json/sqlite/postgres alike in DatasourceRepository), so
    re-selecting the same table was slow every single time - and, since a
    "Reload" button now exists as the explicit way to force a re-fetch,
    there's no reason to keep silently re-hitting the DB in the background
    just because a table was clicked again.

    Interactive changes within a table - sort, filter, pagination - always
    query live and never touch this cache: only the exact default view
    `DataTab.load()` produces is cached/served (see its `on_first_result`
    callback into `QueryResultPanel.load()`), since those are the same query
    being asked again; anything else is a genuinely different query the user
    is actively requesting.

    Keyed by datasource id (tables) or (datasource id, table)
    (columns/indexes/data), so different datasources - or the same
    datasource reconnected to after edits - don't bleed into each other
    except via an explicit invalidate(). Owned by DataExplorePage for the
    page's lifetime, so it also survives switching away to another
    datasource and back within the same session; the "Reload" button next
    to the datasource name calls invalidate() to force a fresh fetch when
    the user knows the underlying schema or data changed."""

    def __init__(self) -> None:
        self._tables: Dict[int, List[str]] = {}
        self._columns: Dict[Tuple[int, str], List[ColumnInfo]] = {}
        self._indexes: Dict[Tuple[int, str], List[IndexInfo]] = {}
        self._data: Dict[Tuple[int, str], Tuple[int, QueryResult]] = {}

    def get_tables(self, datasource: Datasource) -> Optional[List[str]]:
        return self._tables.get(datasource.id)

    def set_tables(self, datasource: Datasource, tables: List[str]) -> None:
        self._tables[datasource.id] = tables

    def get_columns(self, datasource: Datasource, table: str) -> Optional[List[ColumnInfo]]:
        return self._columns.get((datasource.id, table))

    def set_columns(self, datasource: Datasource, table: str, columns: List[ColumnInfo]) -> None:
        self._columns[(datasource.id, table)] = columns

    def get_indexes(self, datasource: Datasource, table: str) -> Optional[List[IndexInfo]]:
        return self._indexes.get((datasource.id, table))

    def set_indexes(self, datasource: Datasource, table: str, indexes: List[IndexInfo]) -> None:
        self._indexes[(datasource.id, table)] = indexes

    def get_data(self, datasource: Datasource, table: str) -> Optional[Tuple[int, QueryResult]]:
        """Returns (total_records, QueryResult) for `table`'s default view,
        or None if it hasn't been loaded (or was invalidated) this session."""
        return self._data.get((datasource.id, table))

    def set_data(self, datasource: Datasource, table: str, total_records: int, result: QueryResult) -> None:
        self._data[(datasource.id, table)] = (total_records, result)

    def invalidate(self, datasource: Datasource) -> None:
        """Drop every cached entry for `datasource` - its table list and
        every table's columns/indexes/default-view data - so the next load
        re-fetches everything from scratch."""
        self._tables.pop(datasource.id, None)
        for key in [k for k in self._columns if k[0] == datasource.id]:
            del self._columns[key]
        for key in [k for k in self._indexes if k[0] == datasource.id]:
            del self._indexes[key]
        for key in [k for k in self._data if k[0] == datasource.id]:
            del self._data[key]


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
    """Table data view: thin wrapper around `QueryResultPanel` (filter/sort/
    pagination pushed down into SQL via `TableQuerySource`) that adds the
    `TableMetadataCache` check for a table's *default* view (page 0, no
    filter, no sort) - see `TableMetadataCache` for why that one exact query
    is worth remembering across table switches while everything else always
    queries live."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        task_manager: TaskManager,
        cache: TableMetadataCache,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._task_manager = task_manager
        self._cache = cache
        self._datasource: Optional[Datasource] = None
        self._table: Optional[str] = None

        sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel = QueryResultPanel(self, task_manager)
        sizer.Add(self._panel, 1, wx.EXPAND)
        self.SetSizer(sizer)

    def load(self, datasource: Datasource, table: str) -> None:
        self._datasource = datasource
        self._table = table
        source = TableQuerySource(self._repository, datasource, table)

        cached_columns = self._cache.get_columns(datasource, table)
        cached_data = self._cache.get_data(datasource, table)
        if cached_columns is not None and cached_data is not None:
            # Both this table's schema AND its default view are cached - no
            # driver round-trip at all, not even a "Querying" task.
            self._panel.load([c.name for c in cached_columns], source, initial=cached_data)
            return

        if cached_columns is not None:
            self._panel.load(
                [c.name for c in cached_columns],
                source,
                error_title="Load table",
                error_message=f'Could not load data for "{table}"',
                task_label=f"Querying: {datasource.name}.{table}",
                on_first_result=lambda total_records, result: self._cache.set_data(
                    datasource, table, total_records, result
                ),
            )
            return

        def on_success(columns: List[ColumnInfo]) -> None:
            if datasource is not self._datasource or table != self._table:
                return  # a different table was selected before this came back
            self._cache.set_columns(datasource, table, columns)
            # Deferred via CallAfter: this callback runs before TaskManager
            # resets itself back to IDLE (that happens right after this
            # returns) - calling self._panel.load (which starts another
            # task) synchronously here would find it still marked busy and
            # be silently ignored.
            wx.CallAfter(
                self._panel.load,
                [c.name for c in columns],
                source,
                error_title="Load table",
                error_message=f'Could not load data for "{table}"',
                task_label=f"Querying: {datasource.name}.{table}",
                on_first_result=lambda total_records, result: self._cache.set_data(
                    datasource, table, total_records, result
                ),
            )

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Could not load columns for \"{table}\":\n\n{exc}", "Load table", wx.OK | wx.ICON_ERROR, self)

        self._task_manager.start(
            label=f"Loading columns: {datasource.name}.{table}",
            work=lambda: self._repository.list_columns(datasource, table),
            on_success=on_success,
            on_error=on_error,
        )

    def clear(self) -> None:
        self._datasource = None
        self._table = None
        self._panel.clear()


class TableDetailPanel(wx.Panel):
    """Fields / Data / Indexes tabs for whichever table is selected."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        task_manager: TaskManager,
        cache: TableMetadataCache,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._task_manager = task_manager
        self._cache = cache

        notebook = wx.Notebook(self)
        self._fields_tab = FieldsTab(notebook)
        self._data_tab = DataTab(notebook, repository, task_manager, cache)
        self._indexes_tab = IndexesTab(notebook)
        notebook.AddPage(self._fields_tab, "Fields")
        notebook.AddPage(self._data_tab, "Data")
        notebook.AddPage(self._indexes_tab, "Indexes")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(notebook, 1, wx.EXPAND)
        self.SetSizer(sizer)

    def load_table(self, datasource: Datasource, table: str) -> None:
        self.clear()

        cached_columns = self._cache.get_columns(datasource, table)
        cached_indexes = self._cache.get_indexes(datasource, table)
        if cached_columns is not None and cached_indexes is not None:
            self._fields_tab.load(cached_columns)
            self._indexes_tab.load(cached_indexes)
            self._data_tab.load(datasource, table)
            return

        def on_success(payload: Tuple[List[ColumnInfo], List[IndexInfo]]) -> None:
            columns, indexes = payload
            self._cache.set_columns(datasource, table, columns)
            self._cache.set_indexes(datasource, table, indexes)
            self._fields_tab.load(columns)
            self._indexes_tab.load(indexes)
            # Deferred via CallAfter - see DataTab.load's comment: TaskManager
            # hasn't reset itself back to IDLE yet at this point in the
            # callback, so calling it synchronously would be ignored.
            wx.CallAfter(self._data_tab.load, datasource, table)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Could not load table \"{table}\":\n\n{exc}", "Load table", wx.OK | wx.ICON_ERROR, self)

        self._task_manager.start(
            label=f"Loading table: {datasource.name}.{table}",
            work=lambda: (
                self._repository.list_columns(datasource, table),
                self._repository.list_indexes(datasource, table),
            ),
            on_success=on_success,
            on_error=on_error,
        )

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

    def load_content(self, content: str, mark_dirty: bool = False) -> None:
        self.Enable(True)
        self.SetReadOnly(False)
        self.SetText(content)
        self.EmptyUndoBuffer()
        if not mark_dirty:
            self.SetSavePoint()


class ScriptsTab(wx.Panel):
    """Scripts belonging to the current datasource: list/create/rename/
    delete saved SQL scripts, edit their content in a syntax-highlighted
    editor, and run either the whole script or just the selected statement
    against the datasource - results render in the same `QueryResultPanel`
    the Data tab uses, backed by a `StaticQuerySource` since a script's SQL
    can be a join/aggregate/DDL statement rather than a plain
    `SELECT * FROM <table>`, so filter/sort/pagination run in Python over
    the already-fetched rows instead of being pushed down into SQL.

    Edits are not persisted until an explicit Save (or the exit "Save All"
    flow) - switching to another script, or to another datasource entirely,
    only stashes the in-progress text in `_pending_edits` (keyed by script
    id, surviving datasource switches) so several scripts across different
    datasources can be "unsaved" at once, which the app-exit confirmation
    needs to be able to list and act on."""

    def __init__(
        self,
        parent: wx.Window,
        script_repository: ScriptRepository,
        datasource_repository: DatasourceRepository,
        task_manager: TaskManager,
    ) -> None:
        super().__init__(parent)
        self._script_repository = script_repository
        self._datasource_repository = datasource_repository
        self._task_manager = task_manager
        self._datasource: Optional[Datasource] = None
        self._scripts: List[Script] = []
        self._current_script: Optional[Script] = None
        self._pending_edits: Dict[int, str] = {}

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
        self._result_panel = QueryResultPanel(splitter)
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
        self._task_manager.subscribe(lambda status, label: self._update_button_states())
        self._update_button_states()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_datasource(self, datasource: Datasource, tables: Optional[List[str]] = None) -> None:
        self._capture_current_edits()
        self._datasource = datasource
        self._ensure_default_script(tables or [])
        self._reload_scripts(select_id=None)

    def clear(self) -> None:
        self._capture_current_edits()
        self._datasource = None
        self._scripts = []
        self._current_script = None
        self._list.Set([])
        self._editor.clear_and_disable()
        self._result_panel.clear()
        self._update_button_states()

    def _ensure_default_script(self, tables: List[str]) -> None:
        """If this datasource has no scripts yet, seed it with one so the
        user always lands on something runnable rather than an empty list."""
        if self._datasource is None or self._script_repository.list(self._datasource.id):
            return
        table_name = tables[0] if tables else "table_name"
        self._script_repository.create(
            Script(
                id=None,
                name="script 1",
                content=f"select * from {table_name}",
                profile_id=self._datasource.profile_id,
                datasource_id=self._datasource.id,
            )
        )

    def _reload_scripts(self, select_id: Optional[int]) -> None:
        self._scripts = self._script_repository.list(self._datasource.id) if self._datasource else []
        self._list.Set([s.name for s in self._scripts])
        index = wx.NOT_FOUND
        if select_id is not None:
            index = next((i for i, s in enumerate(self._scripts) if s.id == select_id), wx.NOT_FOUND)
        elif self._datasource is not None and self._datasource.last_script_id is not None:
            index = next(
                (i for i, s in enumerate(self._scripts) if s.id == self._datasource.last_script_id), wx.NOT_FOUND
            )
        if index == wx.NOT_FOUND and self._scripts:
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
            pending = self._pending_edits.get(script.id)
            if pending is not None:
                self._editor.load_content(pending, mark_dirty=True)
            else:
                self._editor.load_content(script.content)
            if self._datasource is not None:
                self._datasource_repository.set_last_script_id(self._datasource.id, script.id)
        self._result_panel.clear()
        self._update_button_states()

    def focus_script(self, script_id: int) -> None:
        """Select and load `script_id` in this datasource's list - used to
        bring an unsaved script into view (e.g. after the user cancels the
        exit confirmation)."""
        index = next((i for i, s in enumerate(self._scripts) if s.id == script_id), wx.NOT_FOUND)
        if index != wx.NOT_FOUND:
            self._list.SetSelection(index)
            self._load_selected()
        self._list.SetFocus()

    def _update_button_states(self) -> None:
        has_datasource = self._datasource is not None
        has_selection = self._current_script is not None
        # Running/Run Selected are also gated on the app-wide TaskManager,
        # not just this tab's own state - only one task (this script, an
        # export, ...) may run at a time anywhere in the app.
        can_run = has_selection and not self._task_manager.is_busy()
        self._new_btn.Enable(has_datasource)
        self._rename_btn.Enable(has_selection)
        self._delete_btn.Enable(has_selection)
        self._save_btn.Enable(has_selection)
        self._run_all_btn.Enable(can_run)
        self._run_selected_btn.Enable(can_run)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _capture_current_edits(self) -> None:
        """Stash the editor's in-progress text for the current script into
        `_pending_edits` if it differs from what's saved - does not write to
        the database. Called whenever the editor is about to show something
        else (another script, another datasource, or being torn down)."""
        if self._current_script is not None and self._editor.GetModify():
            self._pending_edits[self._current_script.id] = self._editor.GetText()

    def _on_save(self, event: wx.CommandEvent) -> None:
        if self._current_script is None:
            return
        self._current_script.content = self._editor.GetText()
        self._script_repository.update(self._current_script)
        self._pending_edits.pop(self._current_script.id, None)
        self._editor.SetSavePoint()

    def _on_script_selected(self, event: wx.CommandEvent) -> None:
        self._capture_current_edits()
        self._load_selected()

    # ------------------------------------------------------------------
    # Unsaved scripts (used by MainFrame's exit confirmation)
    # ------------------------------------------------------------------
    def list_unsaved_scripts(self) -> List[Script]:
        self._capture_current_edits()
        scripts = []
        for script_id in self._pending_edits:
            script = self._script_repository.get(script_id)
            if script is not None:
                scripts.append(script)
        return scripts

    def save_all_unsaved_scripts(self) -> None:
        self._capture_current_edits()
        for script_id, content in list(self._pending_edits.items()):
            script = self._script_repository.get(script_id)
            if script is None:
                continue
            script.content = content
            self._script_repository.update(script)
        self._pending_edits.clear()
        if self._current_script is not None:
            self._editor.SetSavePoint()

    def discard_all_unsaved_scripts(self) -> None:
        self._pending_edits.clear()
        if self._current_script is not None:
            fresh = self._script_repository.get(self._current_script.id)
            if fresh is not None:
                self._current_script = fresh
                self._editor.load_content(fresh.content)

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
        self._capture_current_edits()
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
        self._pending_edits.pop(script.id, None)
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

        datasource = self._datasource
        cancel_token = CancelToken()

        def work() -> Optional[QueryResult]:
            result = None
            for statement in statements:
                if cancel_token.is_cancelled:
                    break
                result = self._datasource_repository.execute_sql(datasource, statement, cancel_token=cancel_token)
            return result

        def on_success(result: Optional[QueryResult]) -> None:
            if result is not None:
                self._result_panel.load(result.columns, StaticQuerySource(result))

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Query failed:\n\n{exc}", "Execution error", wx.OK | wx.ICON_ERROR, self)

        def on_cancelled() -> None:
            wx.MessageBox("Query cancelled.", "Run", wx.OK | wx.ICON_INFORMATION, self)

        started = self._task_manager.start(
            label=f"Run script on {datasource.name}",
            work=work,
            on_success=on_success,
            on_error=on_error,
            on_cancelled=on_cancelled,
            on_cancel_requested=cancel_token.cancel,
        )
        if not started:
            wx.MessageBox(
                "Another task is already running - wait for it to finish or cancel it first.",
                "Run",
                wx.OK | wx.ICON_WARNING,
                self,
            )


class ActionsTab(wx.Panel):
    """Export actions for the current datasource - both go through
    DatasourceRepository's export_to_parquet/export_schema_to_parquet, which
    work the same way regardless of datasource type (csv, json, postgres,
    ...)."""

    def __init__(self, parent: wx.Window, repository: DatasourceRepository, task_manager: TaskManager) -> None:
        super().__init__(parent)
        self._repository = repository
        self._task_manager = task_manager
        self._datasource: Optional[Datasource] = None

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Export"), 0, wx.ALL, 12)

        self._export_data_btn = wx.Button(self, label="Export as Parquet...")
        self._export_schema_btn = wx.Button(self, label="Export schema as Parquet...")
        outer.Add(self._export_data_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        outer.Add(self._export_schema_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._export_data_btn.Bind(wx.EVT_BUTTON, self._on_export_data)
        self._export_schema_btn.Bind(wx.EVT_BUTTON, self._on_export_schema)
        self._task_manager.subscribe(lambda status, label: self._update_button_states())

    def load_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self._update_button_states()

    def _update_button_states(self) -> None:
        # Export is also gated on the app-wide TaskManager, not just this
        # tab's own state - only one task (this export, a running script,
        # ...) may run at a time anywhere in the app.
        can_run = self._datasource is not None and not self._task_manager.is_busy()
        self._export_data_btn.Enable(can_run)
        self._export_schema_btn.Enable(can_run)

    def _start_or_warn(self, **start_kwargs) -> None:
        if not self._task_manager.start(**start_kwargs):
            wx.MessageBox(
                "Another task is already running - wait for it to finish or cancel it first.",
                "Export",
                wx.OK | wx.ICON_WARNING,
                self,
            )

    def _on_export_data(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        dlg = wx.DirDialog(self, "Choose a folder to export Parquet files into")
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            output_dir = dlg.GetPath()
        finally:
            dlg.Destroy()

        datasource = self._datasource
        cancel_token = CancelToken()

        def on_success(written: List[str]) -> None:
            wx.MessageBox(
                f"Exported {len(written)} table(s) to:\n\n{output_dir}",
                "Export as Parquet",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Export failed:\n\n{exc}", "Export as Parquet", wx.OK | wx.ICON_ERROR, self)

        def on_cancelled() -> None:
            wx.MessageBox("Export cancelled.", "Export as Parquet", wx.OK | wx.ICON_INFORMATION, self)

        self._start_or_warn(
            label=f"Export {datasource.name} as Parquet",
            work=lambda: self._repository.export_to_parquet(datasource, output_dir, cancel_token=cancel_token),
            on_success=on_success,
            on_error=on_error,
            on_cancelled=on_cancelled,
            on_cancel_requested=cancel_token.cancel,
        )

    def _on_export_schema(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        dlg = wx.FileDialog(
            self,
            "Save schema as Parquet",
            wildcard="Parquet files (*.parquet)|*.parquet",
            defaultFile=f"{self._datasource.name}_schema.parquet",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            output_path = dlg.GetPath()
        finally:
            dlg.Destroy()
        if not output_path.lower().endswith(".parquet"):
            output_path += ".parquet"

        datasource = self._datasource
        cancel_token = CancelToken()

        def on_success(_: None) -> None:
            wx.MessageBox(
                f"Schema exported to:\n\n{output_path}",
                "Export schema as Parquet",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Export failed:\n\n{exc}", "Export schema as Parquet", wx.OK | wx.ICON_ERROR, self)

        def on_cancelled() -> None:
            wx.MessageBox("Export cancelled.", "Export schema as Parquet", wx.OK | wx.ICON_INFORMATION, self)

        self._start_or_warn(
            label=f"Export {datasource.name} schema as Parquet",
            work=lambda: self._repository.export_schema_to_parquet(datasource, output_path, cancel_token=cancel_token),
            on_success=on_success,
            on_error=on_error,
            on_cancelled=on_cancelled,
            on_cancel_requested=cancel_token.cancel,
        )


class DataExplorePage(wx.Panel):
    """Reached via "Connect" on the Datasources screen (not a sidebar
    destination): a "Tables" tab lists the connected datasource's tables on
    the left and shows the selected table's Fields/Data/Indexes on the
    right, a "Scripts" tab manages saved SQL scripts for it, and an
    "Actions" tab exports it (data or schema) to Parquet."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        script_repository: ScriptRepository,
        task_manager: TaskManager,
        on_back: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._task_manager = task_manager
        self._on_back = on_back
        self._cache = TableMetadataCache()
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
        header.AddStretchSpacer()
        self._reload_btn = wx.Button(self, label="Reload")
        self._reload_btn.SetToolTip("Discard cached tables/columns/indexes and re-fetch them from the datasource")
        self._reload_btn.Enable(False)
        header.Add(self._reload_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(header, 0, wx.EXPAND | wx.ALL, 12)

        self._notebook = notebook = wx.Notebook(self)

        tables_panel = wx.Panel(notebook)
        body = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(tables_panel, label="Tables"), 0, wx.BOTTOM, 4)
        self._tables_list = wx.ListBox(tables_panel, choices=[], size=(220, -1))
        left.Add(self._tables_list, 1, wx.EXPAND)
        body.Add(left, 0, wx.EXPAND | wx.RIGHT, 12)

        self._detail = TableDetailPanel(tables_panel, repository, task_manager, self._cache)
        body.Add(self._detail, 1, wx.EXPAND)

        tables_outer = wx.BoxSizer(wx.VERTICAL)
        tables_outer.Add(body, 1, wx.EXPAND | wx.ALL, 12)
        tables_panel.SetSizer(tables_outer)
        notebook.AddPage(tables_panel, "Tables")

        self._scripts_tab = ScriptsTab(notebook, script_repository, repository, task_manager)
        notebook.AddPage(self._scripts_tab, "Scripts")

        self._actions_tab = ActionsTab(notebook, repository, task_manager)
        notebook.AddPage(self._actions_tab, "Actions")

        outer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(outer)

        self._back_btn.Bind(wx.EVT_BUTTON, lambda evt: self._on_back())
        self._reload_btn.Bind(wx.EVT_BUTTON, self._on_reload)
        self._tables_list.Bind(wx.EVT_LISTBOX, self._on_table_selected)
        self._task_manager.subscribe(lambda status, label: self._update_reload_button_state())

    def load_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        # Must happen here, synchronously on the UI thread - list_tables
        # below, and everything list_tables leads into afterwards (loading
        # columns/indexes, running scripts, export), runs on a background
        # thread via TaskManager and can't touch the shared sqlite3
        # connection itself (see warm_column_types).
        self._repository.warm_column_types(datasource)
        self._title_label.SetLabel(f"Data Explore — {datasource.name}")
        self._tables_list.Set([])
        self._detail.clear()
        self._update_reload_button_state()

        cached_tables = self._cache.get_tables(datasource)
        if cached_tables is not None:
            self._on_tables_loaded(datasource, cached_tables)
            return

        def on_success(tables: List[str]) -> None:
            if datasource is not self._datasource:
                return  # the user connected to a different datasource before this came back
            self._cache.set_tables(datasource, tables)
            # Deferred via CallAfter - see DataTab.load's comment:
            # TaskManager hasn't reset itself back to IDLE yet at this point
            # in the callback, and _on_tables_loaded may itself start
            # another task (loading the first table) if it isn't cached.
            wx.CallAfter(self._on_tables_loaded, datasource, tables)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not load tables for "{datasource.name}":\n\n{exc}', "Load datasource", wx.OK | wx.ICON_ERROR, self
            )

        self._task_manager.start(
            label=f"Loading tables: {datasource.name}",
            work=lambda: self._repository.list_tables(datasource),
            on_success=on_success,
            on_error=on_error,
        )

    def _on_tables_loaded(self, datasource: Datasource, tables: List[str]) -> None:
        self._tables = tables
        self._tables_list.Set(self._tables)
        if self._tables:
            self._tables_list.SetSelection(0)
            self._detail.load_table(datasource, self._tables[0])
        self._scripts_tab.load_datasource(datasource, self._tables)
        self._actions_tab.load_datasource(datasource)

    def _on_table_selected(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        index = self._tables_list.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self._detail.load_table(self._datasource, self._tables[index])

    # ------------------------------------------------------------------
    # Reload - discards this datasource's cached tables/columns/indexes
    # (see TableMetadataCache) and re-fetches everything from scratch.
    # ------------------------------------------------------------------
    def _on_reload(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        self._cache.invalidate(self._datasource)
        self.load_datasource(self._datasource)

    def _update_reload_button_state(self) -> None:
        self._reload_btn.Enable(self._datasource is not None and not self._task_manager.is_busy())

    # ------------------------------------------------------------------
    # Unsaved scripts (used by MainFrame's exit confirmation)
    # ------------------------------------------------------------------
    def list_unsaved_scripts(self) -> List[Script]:
        return self._scripts_tab.list_unsaved_scripts()

    def save_all_unsaved_scripts(self) -> None:
        self._scripts_tab.save_all_unsaved_scripts()

    def discard_all_unsaved_scripts(self) -> None:
        self._scripts_tab.discard_all_unsaved_scripts()

    def focus_script(self, script_id: int) -> None:
        self._notebook.SetSelection(1)  # Scripts tab
        self._scripts_tab.focus_script(script_id)
