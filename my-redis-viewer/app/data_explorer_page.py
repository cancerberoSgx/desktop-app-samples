import fnmatch
from typing import Callable, Dict, List, Optional

import wx

from .async_task import AsyncTaskRunner
from .indexes_view import IndexesView
from .key_details_dialog import KeyDetailsDialog
from .key_list_ctrl import KeyListCtrl
from .models import Datasource
from .redis_key_tree import build_key_tree
from .repositories import DatasourceRepository, ScriptRepository
from .scripts_view import ScriptsView
from .stats_view import StatsView


class KeyTreeView(wx.Panel):
    """Left: a lazily-populated tree of colon-delimited key branches.
    Right: the leaf keys of whichever branch is selected - double-clicking
    one opens KeyDetailsDialog. The tree is built once from an in-memory
    prefix trie (see redis_key_tree.py), so expanding a branch or selecting
    one is just a dict lookup - no further Redis round-trips."""

    def __init__(self, parent: wx.Window, on_activate_key: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self._tree = wx.TreeCtrl(
            splitter,
            style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.BORDER_SUNKEN,
        )
        self._list = KeyListCtrl(splitter, on_activate_key=on_activate_key)
        splitter.SplitVertically(self._tree, self._list, 280)
        splitter.SetMinimumPaneSize(150)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(splitter, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self._tree.Bind(wx.EVT_TREE_ITEM_EXPANDING, self._on_expanding)
        self._tree.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_select)

    def clear(self) -> None:
        self._tree.DeleteAllItems()
        self._list.set_keys([])

    def load_tree(self, root_node: Dict) -> None:
        self._tree.DeleteAllItems()
        self._list.set_keys([])
        hidden_root = self._tree.AddRoot("root")
        self._add_children(hidden_root, root_node)

    def _add_children(self, parent_item: wx.TreeItemId, node: Dict) -> None:
        for segment in sorted(node["children"]):
            child = node["children"][segment]
            label = f"{segment} ({len(child['leaves'])})" if child["leaves"] else segment
            item = self._tree.AppendItem(parent_item, label)
            self._tree.SetItemData(item, child)
            if child["children"]:
                self._tree.AppendItem(item, "")  # dummy placeholder for lazy expansion

    def _on_expanding(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        node = self._tree.GetItemData(item)
        if node is None:
            return
        first_child, _cookie = self._tree.GetFirstChild(item)
        if first_child.IsOk() and self._tree.GetItemData(first_child) is None:
            self._tree.Delete(first_child)
            self._add_children(item, node)

    def _on_select(self, event: wx.TreeEvent) -> None:
        node = self._tree.GetItemData(event.GetItem())
        self._list.set_keys(sorted(node["leaves"]) if node else [])


class KeySearchView(wx.Panel):
    """Search box (Redis glob pattern, e.g. "doc:foo:*") plus an optional
    type filter. Pattern-only searches filter the key list already cached
    from the Tree tab's scan - instant, no Redis round-trip. Turning on a
    type filter switches to a live, debounced SCAN ... TYPE ... call
    instead, since key types aren't captured by that initial scan (see
    DatasourceRepository.search_keys)."""

    TYPE_CHOICES = ["Any type", "string", "hash", "list", "set", "zset", "stream"]
    DEBOUNCE_MS = 250

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        on_activate_key: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._datasource: Optional[Datasource] = None
        self._cached_keys: List[str] = []
        self._cached_truncated = False
        self._async = AsyncTaskRunner(self)
        self._timer = wx.Timer(self)

        sizer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(wx.StaticText(self, label="Pattern:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._pattern_ctrl = wx.SearchCtrl(self)
        self._pattern_ctrl.ShowCancelButton(True)
        self._pattern_ctrl.SetDescriptiveText("e.g. doc:foo:*")
        toolbar.Add(self._pattern_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        toolbar.Add(wx.StaticText(self, label="Type:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._type_choice = wx.Choice(self, choices=self.TYPE_CHOICES)
        self._type_choice.SetSelection(0)
        toolbar.Add(self._type_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 12)

        self._status = wx.StaticText(self, label="")
        sizer.Add(self._status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = KeyListCtrl(self, on_activate_key=on_activate_key)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(sizer)

        self._pattern_ctrl.Bind(wx.EVT_TEXT, self._on_query_changed)
        self._pattern_ctrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_cancel)
        self._type_choice.Bind(wx.EVT_CHOICE, self._on_query_changed)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)

    def set_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource

    def set_cached_keys(self, keys: List[str], truncated: bool) -> None:
        """Called after the Tree tab's scan completes so pattern-only
        searches have something to filter without hitting Redis again."""
        self._cached_keys = keys
        self._cached_truncated = truncated
        if self._selected_type() is None:
            self._run_search()

    def clear(self) -> None:
        self._timer.Stop()
        self._pattern_ctrl.SetValue("")
        self._type_choice.SetSelection(0)
        self._list.set_keys([])
        self._status.SetLabel("")

    def _on_cancel(self, event: wx.CommandEvent) -> None:
        self._pattern_ctrl.SetValue("")

    def _on_query_changed(self, event: wx.CommandEvent) -> None:
        self._timer.Stop()
        self._timer.StartOnce(self.DEBOUNCE_MS)

    def _on_timer(self, event: wx.TimerEvent) -> None:
        self._run_search()

    def _selected_type(self) -> Optional[str]:
        index = self._type_choice.GetSelection()
        return None if index <= 0 else self.TYPE_CHOICES[index]

    def _run_search(self) -> None:
        if self._datasource is None:
            return
        pattern = self._pattern_ctrl.GetValue().strip() or "*"
        redis_type = self._selected_type()

        if redis_type is None:
            matched = [key for key in self._cached_keys if fnmatch.fnmatchcase(key, pattern)]
            self._list.set_keys(matched)
            suffix = " (tree scan was truncated - results may be incomplete)" if self._cached_truncated else ""
            self._status.SetLabel(f"{len(matched):,} matches{suffix}")
            return

        datasource = self._datasource
        self._status.SetLabel("Searching...")

        def on_success(result) -> None:
            self._list.set_keys(result.keys)
            suffix = " (truncated)" if result.truncated else ""
            self._status.SetLabel(f"{len(result.keys):,} matches{suffix}")

        def on_error(exc: Exception) -> None:
            self._status.SetLabel("Search failed")
            wx.MessageBox(
                f"Search failed:\n\n{exc}",
                "Search failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.search_keys(datasource, pattern, redis_type),
            on_success=on_success,
            on_error=on_error,
        )


class DataExplorerPage(wx.Panel):
    """Opened via "Connect" on the Data Sources page (replacing the old
    PING-only message box). Scans the connected server's keyspace on a
    background thread and renders it as a branch tree - see
    KeyTreeView/redis_key_tree.build_key_tree for how "doc:foo:asdasd"
    becomes branches "doc" and "doc:foo" with the full key as a leaf."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        script_repository: ScriptRepository,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._on_status = on_status or (lambda text: None)
        self._datasource: Optional[Datasource] = None
        self._async = AsyncTaskRunner(self)

        sizer = wx.BoxSizer(wx.VERTICAL)

        self._title = wx.StaticText(self, label="Data Explorer")
        font = self._title.GetFont()
        font.MakeBold()
        self._title.SetFont(font)
        sizer.Add(self._title, 0, wx.ALL, 12)

        notebook = wx.Notebook(self)
        self._tree_view = KeyTreeView(notebook, on_activate_key=self._on_key_activated)
        notebook.AddPage(self._tree_view, "Tree")
        self._search_view = KeySearchView(notebook, repository, on_activate_key=self._on_key_activated)
        notebook.AddPage(self._search_view, "Search")
        self._scripts_view = ScriptsView(notebook, repository, script_repository)
        notebook.AddPage(self._scripts_view, "Scripts")
        self._indexes_view = IndexesView(notebook, repository)
        notebook.AddPage(self._indexes_view, "Indexes")
        self._stats_view = StatsView(notebook, repository)
        notebook.AddPage(self._stats_view, "Stats")
        sizer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(sizer)

    def _on_key_activated(self, key: str) -> None:
        if self._datasource is None:
            return
        dlg = KeyDetailsDialog(self, self._repository, self._datasource, key)
        dlg.ShowModal()
        dlg.Destroy()

    def open_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self._title.SetLabel(f"Data Explorer - {datasource.name}")
        self._tree_view.clear()
        self._search_view.clear()
        self._search_view.set_datasource(datasource)
        self._scripts_view.clear()
        self._scripts_view.set_datasource(datasource)
        self._indexes_view.clear()
        self._indexes_view.set_datasource(datasource)
        self._stats_view.clear()
        self._stats_view.set_datasource(datasource)
        self._on_status("Scanning keys... 0")

        def progress(count: int) -> None:
            wx.CallAfter(self._on_status, f"Scanning keys... {count:,}")

        def on_success(result) -> None:
            self._tree_view.load_tree(build_key_tree(result.keys))
            self._search_view.set_cached_keys(result.keys, result.truncated)
            suffix = " (truncated)" if result.truncated else ""
            self._on_status(f"{len(result.keys):,} keys{suffix}")

        def on_error(exc: Exception) -> None:
            self._on_status("Key scan failed")
            wx.MessageBox(
                f'Could not scan keys on "{datasource.name}":\n\n{exc}',
                "Key scan failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.scan_keys(datasource, on_progress=progress),
            on_success=on_success,
            on_error=on_error,
        )
