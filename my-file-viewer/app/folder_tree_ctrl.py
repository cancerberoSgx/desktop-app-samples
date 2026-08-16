from typing import Callable, List, Optional

import wx
import wx.dataview as dv

from .formatting import format_bytes, format_modified
from .models import FileEntry, FolderListing

# Column indices, index-aligned to _COLUMNS and _SORT_KEYS below.
COL_NAME, COL_SIZE, COL_MODIFIED = range(3)
_COLUMNS = [
    ("Name", 360),
    ("Size", 110),
    ("Modified", 160),
]

# Sort key per column - each returns the raw (unformatted) value to compare,
# so e.g. Size sorts numerically, not on the "12.3 KB" display string, and
# Modified sorts on the epoch float, not the formatted date string.
_SORT_KEYS: List[Callable[[FileEntry], object]] = [
    lambda e: e.name.lower(),
    lambda e: -1 if e.size_bytes is None else e.size_bytes,
    lambda e: -1.0 if e.modified_at is None else e.modified_at,
]

_LOADING_LABEL = "Loading…"


class _Node:
    """Mirrors one tree item on the Python side - the wx item itself only
    exists once its parent has actually been built/rebuilt (see
    FolderTreeCtrl._build_node), but a _Node is the durable, never-recreated
    object that in-flight expand callbacks close over, so a slow background
    listing can always find out (via `loaded`/`loading`) what's still true
    once it lands - the same stale-result-guard spirit as
    FolderExplorerPage._on_folder_loaded, just per-node instead of per-page.

    `children` and `loaded` are the query cache: once a folder has been
    expanded once, its children are kept here and reused for every later
    re-sort or re-expand, so FileSystemService.list_folder is never called
    twice for the same folder - the whole point of doing this lazily.
    """

    __slots__ = ("entry", "loaded", "loading", "load_error", "children", "wx_item", "expanded")

    def __init__(self, entry: FileEntry) -> None:
        self.entry = entry
        self.loaded = False
        self.loading = False
        self.load_error: Optional[str] = None
        self.children: List["_Node"] = []
        self.wx_item: Optional[dv.TreeListItem] = None
        self.expanded = False


class FolderTreeCtrl(dv.TreeListCtrl):
    """Tree view of the currently open folder - top-level items are its
    immediate children, same as the old flat table, but a subfolder can be
    expanded in place to reveal *its* immediate children instead of
    navigating away from it. Columns (Name, Size, Modified) are click-to-sort,
    ascending/descending toggling on repeat clicks, same as before.

    Performance: a folder's contents are only ever queried when the user
    actually expands it (see `_on_item_expanding`) - expanding shows a single
    "Loading…" placeholder row immediately, which is swapped for the real
    children once FolderExplorerPage's async fetch completes. A folder is
    only ever queried once; expanding it again, or re-sorting, reuses the
    `_Node.children` already cached from the first fetch.

    Sorting deliberately doesn't use wx.dataview's native comparator-driven
    column sort: this wx version calls the comparator and fires
    EVT_TREELIST_COLUMN_SORTED, but doesn't actually reorder what
    GetFirstChild/GetNextSibling traverse, so relying on it would desync our
    own bookkeeping from what's on screen. Instead a header click just flips
    our own _sort_column/_sort_ascending state (identical to the old
    FolderContentsCtrl) and every currently-loaded level is rebuilt from the
    cached _Node tree - no re-querying FileSystemService, ever. Folders sort
    before files regardless of column/direction, via the same two-stable-sort
    technique documented in CLAUDE.md (folders-first must not flip on a
    descending sort).
    """

    def __init__(
        self,
        parent: wx.Window,
        on_activate_entry: Callable[[FileEntry], None],
        on_expand_folder: Callable[[str, Callable[[FolderListing], None]], None],
    ) -> None:
        super().__init__(parent, style=dv.TL_DEFAULT_STYLE)
        # Required for a sortable column to accept header clicks at all - see
        # the class docstring for why we don't rely on its actual ordering.
        # Kept as an attribute (not a bare local) since wx only holds a raw
        # reference to it - letting it get garbage-collected would crash.
        self._comparator = _TextComparator()
        self.SetItemComparator(self._comparator)

        self._roots: List[_Node] = []
        self._sort_column = COL_NAME
        self._sort_ascending = True
        self._on_activate_entry = on_activate_entry
        self._on_expand_folder = on_expand_folder
        self._update_column_headers()

        self.Bind(dv.EVT_TREELIST_COLUMN_SORTED, self._on_column_sorted)
        self.Bind(dv.EVT_TREELIST_ITEM_EXPANDING, self._on_item_expanding)
        self.Bind(dv.EVT_TREELIST_ITEM_ACTIVATED, self._on_item_activated)

    def set_root_entries(self, entries: List[FileEntry]) -> None:
        """Replace the tree with a fresh top-level listing - called whenever
        the *currently open folder itself* changes (navigating up, opening a
        different folder, ...), as opposed to expanding a row in place."""
        self._roots = [_Node(e) for e in entries]
        self._rebuild_all()

    def get_selected_entry(self) -> Optional[FileEntry]:
        item = self.GetSelection()
        if item is None or not item.IsOk():
            return None
        node = self.GetItemData(item)
        return node.entry if isinstance(node, _Node) else None

    # ------------------------------------------------------------------
    # Sorting - see class docstring for why this is fully hand-rolled
    # ------------------------------------------------------------------
    def _on_column_sorted(self, event: dv.TreeListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._update_column_headers()
        self._rebuild_all()

    def _update_column_headers(self) -> None:
        # Deliberately not TreeListCtrl.SetSortColumn(): calling it silently
        # collapses every expanded row (a real, reproducible quirk of this
        # wx version, unrelated to the GetFirstChild-order issue above) - see
        # the class docstring. ClearColumns()+AppendColumn() has no such
        # side effect, so the sort arrow is drawn by hand in the label
        # instead, same as the old FolderContentsCtrl did for its wx.ListCtrl.
        self.ClearColumns()
        for index, (label, width) in enumerate(_COLUMNS):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            self.AppendColumn(label, width=width, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)

    def _sorted(self, nodes: List[_Node]) -> List[_Node]:
        # Two stable passes, not one sort keyed on (group, value) - see
        # FolderContentsCtrl's original docstring/CLAUDE.md: reverse=True
        # would flip the folders-first group order too on a descending sort.
        key_func = _SORT_KEYS[self._sort_column]
        rows = sorted(nodes, key=lambda n: key_func(n.entry), reverse=not self._sort_ascending)
        rows.sort(key=lambda n: 0 if n.entry.is_dir else 1)
        return rows

    # ------------------------------------------------------------------
    # Building / rebuilding the wx tree from the cached _Node tree
    # ------------------------------------------------------------------
    def _rebuild_all(self) -> None:
        selected = self.get_selected_entry()
        selected_path = selected.path if selected else None

        self._snapshot_expanded(self._roots)
        self.DeleteAllItems()
        for node in self._sorted(self._roots):
            self._build_node(self.GetRootItem(), node)

        if selected_path:
            self._reselect(self._roots, selected_path)

    def _snapshot_expanded(self, nodes: List[_Node]) -> None:
        """Records each currently-visible node's actual expanded/collapsed
        state before we tear the wx tree down to rebuild it in sorted order -
        there's no EVT_TREELIST_ITEM_COLLAPSED to track this incrementally,
        so we ask the tree directly, right before it's wiped."""
        for node in nodes:
            if node.wx_item is not None:
                node.expanded = self.IsExpanded(node.wx_item)
            if node.loaded:
                self._snapshot_expanded(node.children)

    def _reselect(self, nodes: List[_Node], path: str) -> None:
        for node in nodes:
            if node.entry.path == path and node.wx_item is not None:
                self.Select(node.wx_item)
                return
            if node.loaded:
                self._reselect(node.children, path)

    def _build_node(self, parent_wx_item: dv.TreeListItem, node: _Node) -> dv.TreeListItem:
        icon = "📁" if node.entry.is_dir else "📄"
        item = self.AppendItem(parent_wx_item, f"{icon} {node.entry.name}")
        self.SetItemText(item, COL_SIZE, format_bytes(node.entry.size_bytes))
        self.SetItemText(item, COL_MODIFIED, format_modified(node.entry.modified_at))
        self.SetItemData(item, node)
        node.wx_item = item

        if not node.entry.is_dir:
            return item
        if not node.loaded:
            self._append_marker(item, _LOADING_LABEL)
        elif node.load_error:
            self._append_marker(item, f"⚠ {node.load_error}")
        else:
            for child in self._sorted(node.children):
                self._build_node(item, child)
            if node.expanded:
                self.Expand(item)
        return item

    def _append_marker(self, parent_wx_item: dv.TreeListItem, label: str) -> None:
        """A non-entry child row (loading placeholder / inline error) - just
        text, no _Node behind it, so activation/expansion ignore it."""
        marker = self.AppendItem(parent_wx_item, label)
        self.SetItemData(marker, None)

    # ------------------------------------------------------------------
    # Lazy expansion - the one place a folder's contents get queried
    # ------------------------------------------------------------------
    def _on_item_expanding(self, event: dv.TreeListEvent) -> None:
        item = event.GetItem()
        node = self.GetItemData(item)
        if not isinstance(node, _Node) or node.loaded or node.loading:
            return  # a marker row, an already-loaded folder, or a fetch already in flight

        node.loading = True
        self._on_expand_folder(node.entry.path, lambda listing: self._on_children_loaded(node, listing))

    def _on_children_loaded(self, node: _Node, listing: FolderListing) -> None:
        if node.wx_item is None:
            return  # the folder was navigated away from before this landed
        node.loading = False
        node.loaded = True
        if listing.error is not None:
            node.load_error = listing.error
            node.children = []
        else:
            node.load_error = None
            node.children = [_Node(e) for e in listing.entries]

        for child_item in self._existing_children(node.wx_item):
            self.DeleteItem(child_item)
        if node.load_error:
            self._append_marker(node.wx_item, f"⚠ {node.load_error}")
        else:
            for child in self._sorted(node.children):
                self._build_node(node.wx_item, child)
        self.Expand(node.wx_item)

    def _existing_children(self, item: dv.TreeListItem) -> List[dv.TreeListItem]:
        children = []
        child = self.GetFirstChild(item)
        while child.IsOk():
            children.append(child)
            child = self.GetNextSibling(child)
        return children

    # ------------------------------------------------------------------
    # Activation (double-click / Enter)
    # ------------------------------------------------------------------
    def _on_item_activated(self, event: dv.TreeListEvent) -> None:
        node = self.GetItemData(event.GetItem())
        if isinstance(node, _Node):
            self._on_activate_entry(node.entry)


class _TextComparator(dv.TreeListItemComparator):
    """Never actually consulted for the final on-screen order (see
    FolderTreeCtrl's docstring) - just enough of a comparator that wx
    accepts a sortable column's header click and fires
    EVT_TREELIST_COLUMN_SORTED, which is all we use it for."""

    def Compare(self, treelist: dv.TreeListCtrl, column: int, first: dv.TreeListItem, second: dv.TreeListItem) -> int:
        first_text = treelist.GetItemText(first, column)
        second_text = treelist.GetItemText(second, column)
        if first_text == second_text:
            return 0
        return -1 if first_text < second_text else 1
