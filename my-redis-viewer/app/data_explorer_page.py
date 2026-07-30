from typing import Callable, Dict, List, Optional

import wx

from .async_task import AsyncTaskRunner
from .models import Datasource
from .redis_key_tree import build_key_tree
from .repositories import DatasourceRepository


class KeyListCtrl(wx.ListCtrl):
    """Virtual list of leaf keys under the selected branch - virtual mode
    keeps this responsive even for branches with a very large number of
    keys, since no per-row wx item is ever created."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.BORDER_SUNKEN)
        self.InsertColumn(0, "Key", width=420)
        self._keys: List[str] = []

    def set_keys(self, keys: List[str]) -> None:
        self._keys = keys
        self.SetItemCount(len(keys))
        self.Refresh()

    def OnGetItemText(self, item: int, column: int) -> str:  # noqa: N802 - wx override
        return self._keys[item]


class KeyTreeView(wx.Panel):
    """Left: a lazily-populated tree of colon-delimited key branches.
    Right: the leaf keys of whichever branch is selected. The tree is
    built once from an in-memory prefix trie (see redis_key_tree.py), so
    expanding a branch or selecting one is just a dict lookup - no further
    Redis round-trips."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self._tree = wx.TreeCtrl(
            splitter,
            style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.BORDER_SUNKEN,
        )
        self._list = KeyListCtrl(splitter)
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
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._on_status = on_status or (lambda text: None)
        self._async = AsyncTaskRunner(self)

        sizer = wx.BoxSizer(wx.VERTICAL)

        self._title = wx.StaticText(self, label="Data Explorer")
        font = self._title.GetFont()
        font.MakeBold()
        self._title.SetFont(font)
        sizer.Add(self._title, 0, wx.ALL, 12)

        notebook = wx.Notebook(self)
        self._tree_view = KeyTreeView(notebook)
        notebook.AddPage(self._tree_view, "Tree")
        sizer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(sizer)

    def open_datasource(self, datasource: Datasource) -> None:
        self._title.SetLabel(f"Data Explorer - {datasource.name}")
        self._tree_view.clear()
        self._on_status("Scanning keys... 0")

        def progress(count: int) -> None:
            wx.CallAfter(self._on_status, f"Scanning keys... {count:,}")

        def on_success(result) -> None:
            self._tree_view.load_tree(build_key_tree(result.keys))
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
