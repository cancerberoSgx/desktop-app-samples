import dataclasses
import os
from typing import Callable, Iterable, List, Optional

import wx
import wx.dataview as dv

from .formatting import format_bytes, format_timestamp
from .models import FileEntry, FolderListing

# Column indices, index-aligned to _COLUMNS and _SORT_KEYS below.
COL_NAME, COL_EXTENSION, COL_SIZE, COL_MODIFIED = range(4)
_COLUMNS = [
    ("Name", 300),
    ("Extension", 90),
    ("Size", 110),
    ("Modified", 160),
]

# Sort key per column - each returns the raw (unformatted) value to compare,
# so e.g. Size sorts numerically, not on the "12.3 KB" display string, and
# Modified sorts on the epoch float, not the formatted date string. Sorting
# by Extension is always on the real extension regardless of whether it's
# currently shown in the Name column (see FolderTreeCtrl.set_show_extensions)
# - hiding it there is a display-only choice, not a reason to change sort
# order out from under the user.
_SORT_KEYS: List[Callable[[FileEntry], object]] = [
    lambda e: e.name.lower(),
    lambda e: e.extension.lower(),
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
    navigating away from it. Columns (Name, Extension, Size, Modified) are
    click-to-sort, ascending/descending toggling on repeat clicks, same as
    before. The Extension column always shows/sorts on the real extension
    (a folder's is always empty) regardless of the "Show file extensions"
    setting, which only affects whether the Name column's own text includes
    it - see `set_show_extensions`/`_name_label`.

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
    breadcrumb. Ctrl+P (`on_quick_search_requested`, FolderExplorerPage's
    `enter_quick_search_mode`) is handled the same direct way, for the same
    reason: confirmed by hand-testing that relying solely on the File
    menu's "\tCtrl+P" accelerator was unreliable whenever this control (not
    the search box) held keyboard focus with a selection - handling it
    here, unskipped, the same as Delete/F2, means it's never at the mercy
    of whatever else Ctrl+P might otherwise resolve to first.

    Right-click (or the keyboard "menu" key) fires `on_context_menu` with
    whatever ends up selected - see `_on_item_context_menu`: a right-click on
    a row that's already part of the current selection leaves that whole
    selection alone (so multi-select-then-right-click doesn't collapse it
    down to one), while right-clicking a row outside the current selection
    replaces it with just that row first, the same convention most file
    managers use.

    Type-ahead find: typing a printable character while the tree has focus
    starts a search - `_on_char` fires `on_search_started(first_char)` and
    hands off entirely from there. This control only ever owns *starting*
    a search; the query text, the box that displays it, and every
    keystroke for the rest of the search (further characters, Backspace,
    Up/Down to cycle matches, Escape/click-away to cancel) belong to
    `FolderExplorerPage._search_box` - a real, focused `wx.TextCtrl` - once
    it takes over. `search(text, advance=...)` and `clear_search()` are
    this control's only two other public pieces of the feature: `search`
    jumps to (advance=0, a fresh/edited query) or cycles to (advance=+1/-1)
    the first currently-*visible* row whose name starts with `text`
    (case-insensitively), and `clear_search` drops the cycling state once
    a search ends. See `FolderExplorerPage._on_tree_search_started` for how
    the handoff and the focused-box/cursor/click-outside-cancels behavior
    actually work.

    Quick search (Ctrl+P / File > Quick Search) reuses the exact same box
    as type-ahead find - `FolderExplorerPage._search_mode` picks which
    behavior a keystroke drives - but this control's own piece of it is
    entirely different: `set_quick_search(query)` filters, rather than
    jumps to, matching rows. A row is kept if its name contains any
    whitespace-separated word of `query` (case-insensitive), OR - so a
    match stays reachable - if it's a folder that's currently loaded and
    expanded and at least one of its kept children is itself kept
    (`_quick_search_keep`, recursive). Like type-ahead find, this only
    ever looks at already-loaded, already-*visible* content (an unexpanded
    folder's not-yet-fetched children are never searched into) - the same
    "never touch FileSystemService just to search" rule the rest of this
    control follows. Filtering happens through the same `_rebuild_all()`
    every other structural change (a folder reload, a re-sort) already
    uses, since hiding a row on a `TreeListCtrl` has no cheaper primitive
    than not building it in the first place - there's no partial-hide call
    to make instead, unlike `apply_rename`/`remove_paths`'s surgical single-
    row edits.

    A matched row's Name column brackets every matched substring in
    ‹guillemets› (`_highlighted_name`) - this wx build's Name column uses a
    fixed icon+text renderer with no per-substring color/bold/markup API
    (confirmed directly: `DataViewIconTextRenderer` has no `EnableMarkup`,
    unlike the plain `DataViewTextRenderer` the other columns use, and
    `TreeListCtrl` itself exposes no per-item font/colour/attr call at
    all) - plain-text bracketing is what's actually achievable here
    without replacing this control's rendering entirely.

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
        show_extensions: bool = True,
        on_search_started: Optional[Callable[[str], None]] = None,
        on_quick_search_requested: Optional[Callable[[], None]] = None,
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
        self._show_extensions = show_extensions
        # Type-ahead find cycling state (see search()'s docstring) - which
        # row Up/Down should move relative to. The query text itself isn't
        # tracked here at all once a search has started - FolderExplorerPage's
        # _search_box owns it (see the class docstring).
        self._search_match_node: Optional[_Node] = None
        # Quick search filter words (lower-cased, whitespace-split) - empty
        # means "no filter active". See set_quick_search/_quick_search_keep
        # and the class docstring's Quick search section.
        self._quick_search_words: List[str] = []
        self._on_activate_entry = on_activate_entry
        self._on_expand_folder = on_expand_folder
        self._on_selection_changed = on_selection_changed or (lambda count: None)
        self._on_context_menu = on_context_menu or (lambda entries: None)
        self._on_delete_requested = on_delete_requested or (lambda: None)
        self._on_rename_requested = on_rename_requested or (lambda: None)
        self._on_search_started = on_search_started or (lambda first_char: None)
        self._on_quick_search_requested = on_quick_search_requested or (lambda: None)
        self._update_column_headers()

        self.Bind(dv.EVT_TREELIST_COLUMN_SORTED, self._on_column_sorted)
        self.Bind(dv.EVT_TREELIST_ITEM_EXPANDING, self._on_item_expanding)
        self.Bind(dv.EVT_TREELIST_ITEM_ACTIVATED, self._on_item_activated)
        self.Bind(dv.EVT_TREELIST_SELECTION_CHANGED, self._on_selection_changed_event)
        self.Bind(dv.EVT_TREELIST_ITEM_CONTEXT_MENU, self._on_item_context_menu)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.Bind(wx.EVT_CHAR, self._on_char)

    def set_root_entries(self, entries: List[FileEntry]) -> None:
        """Replace the tree with a fresh top-level listing - called whenever
        the *currently open folder itself* changes (navigating up, opening a
        different folder, ...), as opposed to expanding a row in place."""
        self.clear_search()
        # Defensive, same spirit as clear_search() above: normally
        # FolderExplorerPage._hide_search_box already clears an active
        # quick search before any navigation reaches here (open_folder
        # calls it unconditionally) - this just guarantees a stale filter
        # from a previous folder can never silently apply to a new one.
        self._quick_search_words = []
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

    def _name_label(self, entry: FileEntry) -> str:
        """The icon + display name for the Name column - `entry.extension`
        is stripped off the end when `self._show_extensions` is False (the
        "Show file extensions" setting - see set_show_extensions). This is
        purely cosmetic: `entry.name`/`entry.path` and the dedicated,
        always-populated Extension column are completely unaffected - only
        what's *displayed* in the Name column changes. When a quick search
        is active, every matched substring still visible in `name` (see
        _highlighted_name) is bracketed - matching itself is always done
        against the real `entry.name`, so a word that only matches inside
        a currently-hidden extension still keeps the row (see
        _quick_search_keep) even though there's nothing left to bracket in
        what's actually displayed."""
        icon = "📁" if entry.is_dir else "📄"
        name = entry.name
        if not self._show_extensions and entry.extension:
            name = name[: -len(entry.extension)]
        if self._quick_search_words:
            name = self._highlighted_name(name)
        return f"{icon} {name}"

    def set_show_extensions(self, show_extensions: bool) -> None:
        """Applies a new "Show file extensions" preference - unlike
        set_root_entries/a folder reload, this never re-queries
        FileSystemService and never rebuilds the tree: every already-built
        row's Name column is just relabeled in place (_relabel_names), since
        the change is purely how a name already in hand is displayed, not
        what data is shown. No-op if the value didn't actually change."""
        if self._show_extensions == show_extensions:
            return
        self._show_extensions = show_extensions
        self._relabel_names(self._roots)

    def _relabel_names(self, nodes: List[_Node]) -> None:
        for node in nodes:
            if node.wx_item is not None:
                self.SetItemText(node.wx_item, COL_NAME, self._name_label(node.entry))
            if node.loaded:
                self._relabel_names(node.children)

    # ------------------------------------------------------------------
    # Quick search (Ctrl+P / File > Quick Search) - see the class
    # docstring's Quick search section for the overall design. Unlike
    # type-ahead find (which only ever selects/scrolls), this filters what
    # _rebuild_all actually builds - _filtered_sorted is the one place
    # every row-building call site (_rebuild_all, _build_node,
    # _on_children_loaded) and _visible_nodes_in_order now go through
    # instead of the bare _sorted they used before this feature existed.
    # ------------------------------------------------------------------
    def set_quick_search(self, query: Optional[str]) -> None:
        """Applies (non-empty `query`) or clears (`None`/blank) the quick
        search filter - always via a full _rebuild_all(), same as a
        show-hidden-files reload: a live filter adds/removes many rows at
        once, and there's no cheaper partial-hide call on a TreeListCtrl
        than simply not building a row in the first place. No-op if the
        effective word list didn't actually change (typing a second space
        in a row, for instance).

        Going from an active filter back to none - Escape, or clicking a
        filtered row (which ends quick search as a side effect of the
        click stealing keyboard focus from the search box, see
        FolderExplorerPage._on_search_box_kill_focus) - is a rebuild from a
        short, filtered list back to the full one, which always starts
        drawing from the top same as any other _rebuild_all call; without
        _ensure_selection_visible below, whatever row the user was just
        looking at (still selected - _rebuild_all's own _reselect already
        preserves that) could end up scrolled off-screen instead of
        staying in view."""
        words = query.lower().split() if query else []
        if words == self._quick_search_words:
            return
        was_active = bool(self._quick_search_words)
        self._quick_search_words = words
        self._rebuild_all()
        if was_active and not words:
            self._ensure_selection_visible()

    def _ensure_selection_visible(self) -> None:
        selections = self.GetSelections()
        if selections:
            self.EnsureVisible(selections[0])

    def _name_matches_quick_search(self, entry: FileEntry) -> bool:
        lname = entry.name.lower()
        return any(word in lname for word in self._quick_search_words)

    def _quick_search_keep(self, node: "_Node") -> bool:
        """Only ever called while a quick search is active (see
        _filtered_sorted) - True if `node` itself matches, or (for an
        already-loaded, already-expanded folder only - never descending
        into unloaded/collapsed content any more than type-ahead find's
        own _visible_nodes_in_order does) at least one of its children is
        itself kept. A folder kept only because of a descendant match is
        never forced open: it's only reachable this way if it was already
        expanded, since an unexpanded folder's children are never
        considered in the first place."""
        if self._name_matches_quick_search(node.entry):
            return True
        if node.entry.is_dir and node.loaded and not node.load_error and node.expanded:
            return any(self._quick_search_keep(child) for child in node.children)
        return False

    def _filtered_sorted(self, nodes: List[_Node]) -> List[_Node]:
        """_sorted(), further dropping whatever the active quick search
        filter excludes - a plain pass-through to _sorted when no quick
        search is active, so switching a row-building call site from
        _sorted to this one never changes behavior outside quick search."""
        rows = self._sorted(nodes)
        if not self._quick_search_words:
            return rows
        return [node for node in rows if self._quick_search_keep(node)]

    def _highlighted_name(self, name: str) -> str:
        """Brackets every substring of `name` matching one of
        `_quick_search_words` in ‹guillemets› - see the class docstring
        for why plain-text bracketing, not color/bold, is what's actually
        achievable on this column. Matches from different words (or
        overlapping occurrences of the same word) are merged into a
        single bracketed span first, so e.g. querying "foo bar" against
        "foobar.txt" brackets the whole overlapping run once
        ("‹foobar›.txt"), not as two adjacent, oddly-nested spans."""
        lname = name.lower()
        spans = []
        for word in self._quick_search_words:
            start = 0
            while True:
                index = lname.find(word, start)
                if index == -1:
                    break
                spans.append((index, index + len(word)))
                start = index + 1  # allow overlapping matches of the same word
        if not spans:
            return name
        spans.sort()
        merged = [spans[0]]
        for start, end in spans[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        pieces = []
        cursor = 0
        for start, end in merged:
            pieces.append(name[cursor:start])
            pieces.append(f"‹{name[start:end]}›")
            cursor = end
        pieces.append(name[cursor:])
        return "".join(pieces)

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
        self.SetItemText(node.wx_item, COL_NAME, self._name_label(node.entry))
        self.SetItemText(node.wx_item, COL_EXTENSION, node.entry.extension)
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
        # DeleteAllItems() just invalidated every wx item this control ever
        # held - clear the whole cached tree's wx_item references (not just
        # the ones about to be rebuilt below) so a node a quick search
        # filter excludes this pass can't leave a dangling reference to a
        # now-destroyed native item lying around on the _Node itself; every
        # kept node gets a fresh one reassigned by _build_node right after.
        self._clear_wx_items(self._roots)
        for node in self._filtered_sorted(self._roots):
            self._build_node(self.GetRootItem(), node)

        if selected_paths:
            self._reselect(self._roots, selected_paths)
        self._notify_selection_changed()

    def _clear_wx_items(self, nodes: List[_Node]) -> None:
        for node in nodes:
            node.wx_item = None
            if node.loaded:
                self._clear_wx_items(node.children)

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
        item = self.AppendItem(parent_wx_item, self._name_label(node.entry))
        self.SetItemText(item, COL_EXTENSION, node.entry.extension)
        self.SetItemText(item, COL_SIZE, format_bytes(node.entry.size_bytes))
        self.SetItemText(item, COL_MODIFIED, format_timestamp(node.entry.modified_at))
        self.SetItemData(item, node)
        node.wx_item = item

        if not node.entry.is_dir:
            return item
        if not node.loaded:
            self._append_marker(item, _LOADING_LABEL)
        elif node.load_error:
            self._append_marker(item, f"⚠ {node.load_error}")
        else:
            for child in self._filtered_sorted(node.children):
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
            for child in self._filtered_sorted(node.children):
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
        elif code == ord("P") and event.ControlDown():
            # Handled directly, the same reason Delete/F2 are above rather
            # than relying on the File menu's "\tCtrl+P" accelerator alone -
            # confirmed by hand-testing that with a row selected (this
            # control - not the search box - holding keyboard focus),
            # Ctrl+P could otherwise get swallowed before it ever reached
            # the frame's accelerator table, instead of reliably opening
            # quick search every time the way clicking the menu item
            # always did. See "A hard 'last Bind() wins' gotcha" and the
            # Delete/F2 bullets in CLAUDE.md for the same class of issue.
            self._on_quick_search_requested()
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
    # Type-ahead find - see the class docstring for the overall design.
    # This control's only jobs: notice the keystroke that starts a search
    # (_on_char) and, once FolderExplorerPage's search box hands a query
    # back (search()), find/select/scroll to a match among currently-
    # visible rows (_visible_nodes_in_order).
    # ------------------------------------------------------------------
    def _on_char(self, event: wx.KeyEvent) -> None:
        """EVT_CHAR (not EVT_KEY_DOWN) is what's bound here: unlike
        EVT_KEY_DOWN's GetUnicodeKey(), which wx explicitly documents as
        normalized (always the *uppercase* form of a letter key, ignoring
        actual shift/caps-lock state), EVT_CHAR's is the real,
        case-correct typed character - needed since the search box must
        display exactly what the user typed, not a case-mangled version of
        it. Space, Delete, and F2 never reach here at all: `_on_key_down`
        already handles all three without calling `event.Skip()`, which is
        what stops wx from ever generating a follow-up EVT_CHAR for that
        same keystroke. Once a search has started, this handler stops
        firing anyway for as long as it lasts - focus moves to the search
        box (see `FolderExplorerPage._on_tree_search_started`), and
        EVT_CHAR only ever fires on whichever widget currently has
        keyboard focus - so this is only ever reached for the single
        keystroke that *starts* a new search."""
        code = event.GetUnicodeKey()
        if (
            code == wx.WXK_NONE
            or code < 32
            or code == 127
            or event.ControlDown()
            or event.AltDown()
            or event.MetaDown()
        ):
            event.Skip()
            return
        self._on_search_started(chr(code))

    def clear_search(self) -> None:
        """Drops the type-ahead cycling state - called once a search ends
        (FolderExplorerPage's search box losing focus, Escape, or emptying
        the query - see `_on_search_box_kill_focus`) and defensively from
        `set_root_entries`, so a stale match from a previous folder's
        listing can never be cycled to relative to a totally different
        folder's contents."""
        self._search_match_node = None

    def _visible_nodes_in_order(self) -> List["_Node"]:
        """Every currently-rendered row, top to bottom, in on-screen order -
        respects the active sort and only descends into a folder that's
        both loaded and currently expanded, since a collapsed or
        not-yet-expanded folder's contents aren't "displayed" - type-ahead
        search (`search`) should never jump to a row the user can't
        actually see on screen. Also respects an active quick search
        filter (_filtered_sorted, not the bare _sorted) - a row a quick
        search has hidden isn't "displayed" either, and has no wx_item to
        jump to even if it were matched here."""
        result: List["_Node"] = []

        def walk(nodes: List["_Node"]) -> None:
            for node in self._filtered_sorted(nodes):
                result.append(node)
                if (
                    node.entry.is_dir
                    and node.loaded
                    and not node.load_error
                    and node.wx_item is not None
                    and self.IsExpanded(node.wx_item)
                ):
                    walk(node.children)

        walk(self._roots)
        return result

    def search(self, text: str, advance: int = 0) -> None:
        """`advance=0` (a fresh or just-edited query) jumps to the FIRST
        currently-visible row whose name starts with `text`
        (case-insensitive) - `advance=+1`/`-1` (Down/Up on
        FolderExplorerPage's search box while it's focused) instead cycles
        to the next/previous match relative to the last one, wrapping
        around. Matched by the actual `_Node` object, not a list index:
        the set of visible rows can change between keystrokes (an
        expand/collapse, a delete, ...), so an index computed last time
        could easily point at the wrong row now - a stale/no-longer-
        matching node object is instead detected via `not in matches` and
        falls back to the first (or last, for a "previous" cycle) match,
        same as a brand new search. Does nothing (leaves the current
        selection alone) if nothing currently visible matches."""
        query = text.lower()
        matches = [node for node in self._visible_nodes_in_order() if node.entry.name.lower().startswith(query)]
        if not matches:
            return
        if advance == 0 or self._search_match_node not in matches:
            target = matches[0] if advance >= 0 else matches[-1]
        else:
            current_pos = matches.index(self._search_match_node)
            target = matches[(current_pos + advance) % len(matches)]
        self._search_match_node = target
        if target.wx_item is not None:
            self.UnselectAll()
            self.Select(target.wx_item)
            self.EnsureVisible(target.wx_item)
            self._notify_selection_changed()

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
