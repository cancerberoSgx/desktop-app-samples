import dataclasses
import os
from typing import Callable, Iterable, List, Optional

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

    Keyboard: Space toggles the selected row(s)' expand/collapse state
    (`_toggle_selected_expand`) - Enter/double-click activates it (opens a
    file, navigates into a folder) via `EVT_TREELIST_ITEM_ACTIVATED`, which
    wx.dataview.TreeListCtrl already fires natively on the Enter key, no
    extra binding needed. Delete and F2 call back out to
    `on_delete_requested`/`on_rename_requested` (FolderExplorerPage's
    `delete_selected`/`rename_selected`) rather than doing anything with the
    selection here directly - this control only ever reports "the user asked
    to do X", never decides whether X is currently legal (single vs. multi
    selection); that policy lives in FolderExplorerPage, the one place that
    also drives the File menu's enabled state for the same actions, so
    there's a single source of truth for "is this action available right
    now" instead of two. `collapse_all()` collapses every expanded row at
    once - wired to FolderExplorerPage's toolbar button next to the
    breadcrumb.

    Right-click (or the keyboard "menu" key) fires `on_context_menu` with
    whatever ends up selected - see `_on_item_context_menu`: a right-click on
    a row that's already part of the current selection leaves that whole
    selection alone (so multi-select-then-right-click doesn't collapse it
    down to one), while right-clicking a row outside the current selection
    replaces it with just that row first, the same convention most file
    managers use.

    Multi-selection (`TL_MULTIPLE`): Up/Down/PageUp/PageDown/Home/End move a
    single selection same as before; holding Shift with any of them extends
    a *range* selection instead, and a plain click/keypress without Shift
    collapses back to a single selection - all of this is native GTK
    TreeView behavior that comes for free once `TL_MULTIPLE` is set, no
    custom key handling needed (confirmed by hand-testing, see "Verification
    performed"). The one thing `TL_MULTIPLE` requires everywhere in this
    class: never call `GetSelection()` (singular) - it hard-asserts once
    `TL_MULTIPLE` is set - always `GetSelections()` (plural), even for "is
    exactly one thing selected" checks.

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
        on_selection_changed: Optional[Callable[[int], None]] = None,
        on_context_menu: Optional[Callable[[List[FileEntry]], None]] = None,
        on_delete_requested: Optional[Callable[[], None]] = None,
        on_rename_requested: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, style=dv.TL_DEFAULT_STYLE | dv.TL_MULTIPLE)
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
        self._on_selection_changed = on_selection_changed or (lambda count: None)
        self._on_context_menu = on_context_menu or (lambda entries: None)
        self._on_delete_requested = on_delete_requested or (lambda: None)
        self._on_rename_requested = on_rename_requested or (lambda: None)
        self._update_column_headers()

        self.Bind(dv.EVT_TREELIST_COLUMN_SORTED, self._on_column_sorted)
        self.Bind(dv.EVT_TREELIST_ITEM_EXPANDING, self._on_item_expanding)
        self.Bind(dv.EVT_TREELIST_ITEM_ACTIVATED, self._on_item_activated)
        self.Bind(dv.EVT_TREELIST_SELECTION_CHANGED, self._on_selection_changed_event)
        self.Bind(dv.EVT_TREELIST_ITEM_CONTEXT_MENU, self._on_item_context_menu)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)

    def set_root_entries(self, entries: List[FileEntry]) -> None:
        """Replace the tree with a fresh top-level listing - called whenever
        the *currently open folder itself* changes (navigating up, opening a
        different folder, ...), as opposed to expanding a row in place."""
        self._roots = [_Node(e) for e in entries]
        self._rebuild_all()

    def get_selected_entries(self) -> List[FileEntry]:
        """All currently-selected entries (plural, even with a single
        selection) - never GetSelection() (singular), which hard-asserts
        once TL_MULTIPLE is set, see the class docstring."""
        entries = []
        for item in self.GetSelections():
            node = self.GetItemData(item)
            if isinstance(node, _Node):
                entries.append(node.entry)
        return entries

    def select_path(self, path: str) -> bool:
        """Selects the top-level row whose entry matches `path` - used after
        navigating to a pasted/typed file path
        (FolderExplorerPage._navigate_to_pasted_path): the file's *parent*
        folder is opened fresh for this, so the target is always a root
        node here already, never a nested one that would need expanding
        first. Select() doesn't fire EVT_TREELIST_SELECTION_CHANGED on its
        own, so this notifies by hand, same as _rebuild_all's _reselect."""
        for node in self._roots:
            if node.entry.path == path and node.wx_item is not None:
                self.UnselectAll()
                self.Select(node.wx_item)
                self.EnsureVisible(node.wx_item)
                self._notify_selection_changed()
                return True
        return False

    def root_count(self) -> int:
        """Number of top-level rows - used to refresh the "N item(s)" header
        note after a delete, without re-querying FileSystemService."""
        return len(self._roots)

    def _find_node(self, path: str, nodes: Optional[List[_Node]] = None) -> Optional["_Node"]:
        """Searches the whole cached tree (every loaded level, not just
        `_roots`) for the node backing `path` - a selected row can be
        nested arbitrarily deep once its ancestors have been expanded."""
        for node in self._roots if nodes is None else nodes:
            if node.entry.path == path:
                return node
            if node.loaded:
                found = self._find_node(path, node.children)
                if found is not None:
                    return found
        return None

    def apply_rename(self, old_path: str, new_path: str) -> None:
        """Updates the row for `old_path` in place to reflect a successful
        FileSystemService.rename - called by
        FolderExplorerPage.rename_selected() once the async rename
        succeeds. Deliberately a surgical `SetItemText` on the existing wx
        item, not a `_rebuild_all()`: a full rebuild tears down and
        re-adds every row, which resets the control's vertical scroll
        position back to the top - jarring for a rename that, in the
        common case, only changes one row's text. The trade-off is that the
        row doesn't jump to its new alphabetically-sorted position right
        away; it'll land there next time the user re-sorts or reloads the
        folder, which is a much smaller cost than losing their scroll
        position on every rename.

        If the renamed entry is a folder that had already been expanded/
        loaded, its cached children are dropped (native rows deleted, then
        replaced with a single "Loading…" placeholder, same as a
        never-expanded folder) rather than patched up in place: renaming a
        folder changes every descendant's real path too (they're now under
        the new name), and patching each one recursively isn't worth it for
        a deliberate, infrequent action - the same trade-off
        FolderExplorerPage.set_show_hidden already makes for a full
        reload."""
        node = self._find_node(old_path)
        if node is None or node.wx_item is None:
            return
        node.entry = dataclasses.replace(node.entry, name=os.path.basename(new_path), path=new_path)
        icon = "📁" if node.entry.is_dir else "📄"
        self.SetItemText(node.wx_item, COL_NAME, f"{icon} {node.entry.name}")
        if node.entry.is_dir and node.loaded:
            for child_item in self._existing_children(node.wx_item):
                self.DeleteItem(child_item)
            self._append_marker(node.wx_item, _LOADING_LABEL)
            node.loaded = False
            node.loading = False
            node.load_error = None
            node.children = []
            node.expanded = False
            self.Collapse(node.wx_item)

    def remove_paths(self, paths: Iterable[str]) -> None:
        """Removes the row(s) for `paths` - called by
        FolderExplorerPage.delete_selected() once the async delete
        succeeds. Deliberately per-node `DeleteItem` calls, not a
        `_rebuild_all()`, for the same reason as apply_rename: a full
        rebuild would reset the vertical scroll position back to the top,
        which is jarring when the user just deleted one row out of a long,
        scrolled-down listing. Also drops each removed node from wherever
        it lives in the cached _Node tree (top-level or an already-loaded
        folder's children) so it can't resurface on some later resort."""
        path_set = set(paths)
        for path in path_set:
            node = self._find_node(path)
            if node is not None and node.wx_item is not None:
                self.DeleteItem(node.wx_item)  # also removes any descendant rows natively
        self._roots = [node for node in self._roots if node.entry.path not in path_set]
        self._remove_from_children(self._roots, path_set)
        self._notify_selection_changed()

    def _remove_from_children(self, nodes: List[_Node], path_set: set) -> None:
        for node in nodes:
            if node.loaded:
                node.children = [child for child in node.children if child.entry.path not in path_set]
                self._remove_from_children(node.children, path_set)

    def collapse_all(self) -> None:
        """Collapses every currently-expanded folder row - just a UI
        collapse, same as clicking each one's arrow individually, so the
        `_Node.children` cache is untouched and re-expanding any of them
        afterwards still won't re-query FileSystemService."""
        self._collapse_all(self._roots)

    def _collapse_all(self, nodes: List[_Node]) -> None:
        for node in nodes:
            if node.entry.is_dir and node.wx_item is not None:
                self.Collapse(node.wx_item)
            if node.loaded:
                self._collapse_all(node.children)

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
        selected_paths = {entry.path for entry in self.get_selected_entries()}

        self._snapshot_expanded(self._roots)
        self.DeleteAllItems()
        for node in self._sorted(self._roots):
            self._build_node(self.GetRootItem(), node)

        if selected_paths:
            self._reselect(self._roots, selected_paths)
        self._notify_selection_changed()

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

    def _reselect(self, nodes: List[_Node], paths: set) -> None:
        """Reselects every node whose path is in `paths` - not just the
        first match found, since a resort/rebuild must preserve a whole
        multi-selection, not collapse it down to one row."""
        for node in nodes:
            if node.entry.path in paths and node.wx_item is not None:
                self.Select(node.wx_item)
            if node.loaded:
                self._reselect(node.children, paths)

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
    # Activation (double-click / Enter), keyboard expand (Space), and
    # Delete/F2 (see the class docstring for why these just call back out
    # rather than deciding anything about the selection here)
    # ------------------------------------------------------------------
    def _on_item_activated(self, event: dv.TreeListEvent) -> None:
        # Enter/double-click - wx.dataview.TreeListCtrl already fires this
        # natively on the Enter key, no extra key binding needed here.
        node = self.GetItemData(event.GetItem())
        if isinstance(node, _Node):
            self._on_activate_entry(node.entry)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_SPACE:
            self._toggle_selected_expand()
        elif code == wx.WXK_DELETE:
            self._on_delete_requested()
        elif code == wx.WXK_F2:
            self._on_rename_requested()
        else:
            event.Skip()

    def _toggle_selected_expand(self) -> None:
        """Space toggles every selected folder row open/closed,
        independently - each keeps its own expand/collapse state rather than
        all following whichever way the first one goes. Expand() fires
        EVT_TREELIST_ITEM_EXPANDING just like an arrow click does, so this
        reuses _on_item_expanding's lazy-load path rather than duplicating
        it; Collapse() is always just a UI collapse (see collapse_all)."""
        for item in self.GetSelections():
            node = self.GetItemData(item)
            if not isinstance(node, _Node) or not node.entry.is_dir:
                continue
            if self.IsExpanded(item):
                self.Collapse(item)
            else:
                self.Expand(item)

    # ------------------------------------------------------------------
    # Context menu (right-click / the keyboard "menu" key)
    # ------------------------------------------------------------------
    def _on_item_context_menu(self, event: dv.TreeListEvent) -> None:
        item = event.GetItem()
        node = self.GetItemData(item)
        if not isinstance(node, _Node):
            return  # a marker row (Loading…/error) - nothing to act on
        if not self.IsSelected(item):
            # Right-clicking a row outside the current selection replaces
            # it with just that row, same convention most file managers
            # use - but right-clicking a row that's already part of the
            # current (possibly multi-row) selection leaves it alone, so
            # "select several rows, then right-click one of them" doesn't
            # collapse the selection down to one.
            self.UnselectAll()
            self.Select(item)
            self._notify_selection_changed()
        self._on_context_menu(self.get_selected_entries())

    # ------------------------------------------------------------------
    # Selection count (for FolderExplorerPage/MainFrame's "Selected: N")
    # ------------------------------------------------------------------
    def _on_selection_changed_event(self, event: dv.TreeListEvent) -> None:
        event.Skip()
        self._notify_selection_changed()

    def _notify_selection_changed(self) -> None:
        self._on_selection_changed(len(self.GetSelections()))


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
