import bisect
import fnmatch
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Set

import wx

from .async_task import AsyncTaskRunner
from .indexes_view import IndexesView
from .key_details_dialog import KeyDetailsDialog
from .key_list_ctrl import KeyListCtrl
from .models import Datasource
from .redis_key_tree import NO_PREFIX_LABEL, insert_key, new_node
from .repositories import DatasourceRepository, ScriptRepository
from .scripts_view import ScriptsView
from .stats_view import StatsView


class _BranchRef(NamedTuple):
    """What a KeyTreeView tree item's data actually holds: the branch's
    full colon-joined path (e.g. "doc:foo", or NO_PREFIX_LABEL for the
    synthetic no-delimiter bucket - see redis_key_tree.py) alongside its
    trie node. The path is only needed for the Delete button's
    confirmation text and its SCAN MATCH pattern; every other tree
    operation only ever cared about the node."""

    path: str
    node: Dict


class KeyTreeView(wx.Panel):
    """Left: a lazily-populated tree of colon-delimited key branches.
    Right: the leaf keys of whichever branch is selected - double-clicking
    one opens KeyDetailsDialog. The tree is built from an in-memory prefix
    trie (see redis_key_tree.py), so expanding a branch or selecting one is
    just a dict lookup - no further Redis round-trips. That trie is filled
    in progressively as the backing scan runs (see begin_scan/add_keys) so
    the tree is browsable well before a slow/large-keyspace scan finishes,
    and it's a snapshot from whenever it was last (re)built - it won't show
    keys created since - `refresh_btn` (wired by DataExplorerPage to the
    same bounded scan_keys() call used at connect time) is the way to pick
    those up on demand, without paying for a live Redis round-trip on
    every expand/select the way the old no-cache design would have."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        on_activate_key: Optional[Callable[[str], None]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._datasource: Optional[Datasource] = None
        self._async = AsyncTaskRunner(self)
        self._on_refresh = on_refresh or (lambda: None)
        self._root: Dict = new_node()
        self._hidden_root: Optional[wx.TreeItemId] = None
        self._top_level_order: List[str] = []
        self._top_level_labels: Set[str] = set()
        self._top_level_items: Dict[str, wx.TreeItemId] = {}
        self._filter_query = ""
        # Node identities (id(node)) the user has expanded - tracked
        # independently of what's currently rendered, so re-filtering
        # (which deletes and re-adds tree items) never loses track of what
        # was open, even if a stricter filter temporarily hides everything.
        self._expanded_node_ids: Set[int] = set()

        sizer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh_btn = wx.Button(self, label="Refresh")
        self.refresh_btn.SetToolTip(
            "Re-scan the keyspace - picks up keys created since this tree was last built."
        )
        toolbar.Add(self.refresh_btn, 0, wx.RIGHT, 8)
        self._filter_ctrl = wx.SearchCtrl(self, size=(200, -1))
        self._filter_ctrl.SetDescriptiveText("Filter tree")
        self._filter_ctrl.ShowCancelButton(True)
        self._filter_ctrl.SetToolTip(
            "Show only branches and keys containing this text - filters the tree as "
            "already built, in memory, without re-scanning Redis. A branch stays "
            "visible if a key nested inside it matches, even if its own name doesn't."
        )
        toolbar.Add(self._filter_ctrl, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(toolbar, 0, wx.ALL, 8)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self._tree = wx.TreeCtrl(
            splitter,
            style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.BORDER_SUNKEN,
        )

        right_panel = wx.Panel(splitter)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._delete_btn = wx.Button(right_panel, label="Delete")
        self._delete_btn.SetToolTip(
            "Delete every key under the selected branch - asks for confirmation and "
            "shows the exact key pattern first."
        )
        self._delete_btn.Enable(False)
        right_toolbar.Add(self._delete_btn, 0)
        right_sizer.Add(right_toolbar, 0, wx.ALL, 8)
        self._list = KeyListCtrl(right_panel, on_activate_key=on_activate_key)
        right_sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        right_panel.SetSizer(right_sizer)

        splitter.SplitVertically(self._tree, right_panel, 280)
        splitter.SetMinimumPaneSize(150)

        sizer.Add(splitter, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda event: self._on_refresh())
        self._delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self._filter_ctrl.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._filter_ctrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_filter_cancel)
        self._tree.Bind(wx.EVT_TREE_ITEM_EXPANDING, self._on_expanding)
        self._tree.Bind(wx.EVT_TREE_ITEM_EXPANDED, self._on_expanded)
        self._tree.Bind(wx.EVT_TREE_ITEM_COLLAPSED, self._on_collapsed)
        self._tree.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_select)

    def set_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource

    def clear(self) -> None:
        self._tree.DeleteAllItems()
        self._list.set_keys([])
        self._root = new_node()
        self._hidden_root = None
        self._top_level_order = []
        self._top_level_labels = set()
        self._top_level_items = {}
        self._filter_query = ""
        self._filter_ctrl.ChangeValue("")
        self._expanded_node_ids = set()
        self._delete_btn.Enable(False)

    def begin_scan(self) -> None:
        """Reset the tree and get ready for add_keys() - call once before
        the first add_keys() of a (re)scan."""
        self.clear()
        self._hidden_root = self._tree.AddRoot("root")

    def add_keys(self, new_keys: List[str]) -> None:
        """Insert one batch of newly-scanned keys into the tree without
        disturbing branches that are already shown. Only genuinely new
        top-level branches become new tree items (inserted in sorted
        position, matching the old all-at-once build); a branch that's
        already shown just gets its "(N)" leaf-count label (and, if it
        just gained its first nested child, its expand arrow) refreshed.
        Already-expanded branches are left alone, so a user browsing while
        the scan is still running doesn't get their place in the tree
        pulled out from under them on every batch. Deeper levels stay
        lazily populated on expand (_on_expanding), reading from the same
        node dicts this mutates in place, same as before."""
        if self._hidden_root is None:
            return
        touched_labels = {insert_key(self._root, key) for key in new_keys}

        selection = self._tree.GetSelection()
        selected_ref = self._tree.GetItemData(selection) if selection.IsOk() else None

        for label in touched_labels:
            node = self._root["children"][label]
            label_text = f"{label} ({len(node['leaves'])})" if node["leaves"] else label
            if label not in self._top_level_labels:
                index = bisect.bisect_left(self._top_level_order, label)
                self._top_level_order.insert(index, label)
                self._top_level_labels.add(label)
            item = self._top_level_items.get(label)
            if self._branch_relevant(label, node):
                if item is None:
                    item = self._tree.InsertItem(self._hidden_root, self._visible_index(label), label_text)
                    self._tree.SetItemData(item, _BranchRef(label, node))
                    self._top_level_items[label] = item
                else:
                    self._tree.SetItemText(item, label_text)
                if node["children"]:
                    first_child, _cookie = self._tree.GetFirstChild(item)
                    if not first_child.IsOk():
                        self._tree.AppendItem(item, "")  # dummy placeholder for lazy expansion
            elif item is not None:
                # was shown, no longer matches the active filter - drop the
                # tree item but keep the node data (self._root), so it comes
                # back the moment the filter no longer excludes it.
                self._tree.Delete(item)
                del self._top_level_items[label]
            if selected_ref is not None and node is selected_ref.node:
                self._set_list_from_node(node)

    def _matches_text(self, text: str) -> bool:
        return self._filter_query in text.lower()

    def _branch_relevant(self, label: str, node: Dict) -> bool:
        """True if this branch belongs on screen under the active filter:
        its own label matches, one of its already-known leaf keys
        matches, or (recursively) a descendant branch does. This walks
        the already-scanned in-memory trie (self._root and its nested
        node dicts) - the whole keyspace already sitting in memory from
        the last scan/expand, never Redis - so a branch containing a
        deeply-nested match stays reachable even though its own label
        doesn't match. It only decides what's *worth* rendering; actually
        materializing it into the wx.TreeCtrl still happens lazily/on
        expand exactly as before, so an irrelevant branch never gets
        rendered just to answer this question."""
        if not self._filter_query:
            return True
        if self._matches_text(label):
            return True
        if any(self._matches_text(leaf) for leaf in node["leaves"]):
            return True
        return any(
            self._branch_relevant(child_label, child_node)
            for child_label, child_node in node["children"].items()
        )

    def _set_list_from_node(self, node: Dict) -> None:
        """Populate the right-hand key list from `node`'s leaves,
        respecting the active filter (so selecting a branch that's only
        shown because a nested descendant matches doesn't dump every
        unrelated key in that branch into the list)."""
        leaves = node["leaves"]
        if self._filter_query:
            leaves = [leaf for leaf in leaves if self._matches_text(leaf)]
        self._list.set_keys(sorted(leaves))

    def _visible_index(self, label: str) -> int:
        """Position `label` should land at among the top-level items
        currently shown (i.e. respecting the active filter) - the ones
        before it in sorted order that the filter also lets through."""
        pos = bisect.bisect_left(self._top_level_order, label)
        return sum(
            1
            for prior in self._top_level_order[:pos]
            if self._branch_relevant(prior, self._root["children"][prior])
        )

    def _on_expanded(self, event: wx.TreeEvent) -> None:
        ref = self._tree.GetItemData(event.GetItem())
        if ref is not None:
            self._expanded_node_ids.add(id(ref.node))

    def _on_collapsed(self, event: wx.TreeEvent) -> None:
        ref = self._tree.GetItemData(event.GetItem())
        if ref is not None:
            self._expanded_node_ids.discard(id(ref.node))

    def _add_children(
        self,
        parent_item: wx.TreeItemId,
        node: Dict,
        parent_path: str,
        expanded_ids: FrozenSet[int] = frozenset(),
    ) -> None:
        """Render `node`'s children under `parent_item`, skipping any
        branch the active filter excludes (own label, its leaves, and its
        own descendants all considered - see _branch_relevant). A child
        whose node id is in `expanded_ids` (i.e. it was already expanded
        before a filter change triggered a re-render) is rendered
        eagerly, recursing into its own children the same way, so
        re-filtering never collapses a branch the user already had open."""
        for segment in sorted(node["children"]):
            child = node["children"][segment]
            if not self._branch_relevant(segment, child):
                continue
            label = f"{segment} ({len(child['leaves'])})" if child["leaves"] else segment
            child_path = f"{parent_path}:{segment}"
            item = self._tree.AppendItem(parent_item, label)
            self._tree.SetItemData(item, _BranchRef(child_path, child))
            if child["children"]:
                if id(child) in expanded_ids:
                    self._add_children(item, child, child_path, expanded_ids)
                    self._tree.Expand(item)
                else:
                    self._tree.AppendItem(item, "")  # dummy placeholder for lazy expansion

    def _on_expanding(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        ref = self._tree.GetItemData(item)
        if ref is None:
            return
        first_child, _cookie = self._tree.GetFirstChild(item)
        if first_child.IsOk() and self._tree.GetItemData(first_child) is None:
            self._tree.Delete(first_child)
            self._add_children(item, ref.node, ref.path)

    def _on_select(self, event: wx.TreeEvent) -> None:
        ref = self._tree.GetItemData(event.GetItem())
        if ref is None:
            self._list.set_keys([])
            self._delete_btn.Enable(False)
        else:
            self._set_list_from_node(ref.node)
            self._delete_btn.Enable(True)

    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self._filter_query = self._filter_ctrl.GetValue().strip().lower()
        self._apply_filter()

    def _on_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._filter_ctrl.ChangeValue("")
        self._filter_query = ""
        self._apply_filter()

    def _find_item_for_node(self, item: wx.TreeItemId, target_node: Dict) -> Optional[wx.TreeItemId]:
        """Search the tree items already rendered under `item` for the one
        holding `target_node` - used to re-find the previously-selected
        branch after a filter re-render rebuilds the tree's items, and to
        locate a branch's current tree item when it's deleted."""
        ref = self._tree.GetItemData(item)
        if ref is not None and ref.node is target_node:
            return item
        child, cookie = self._tree.GetFirstChild(item)
        while child.IsOk():
            found = self._find_item_for_node(child, target_node)
            if found is not None:
                return found
            child, cookie = self._tree.GetNextChild(item, cookie)
        return None

    def _apply_filter(self) -> None:
        """Re-render the tree from what's already known - self._root (the
        full in-memory trie built from the scan so far) for top-level
        branches, and whatever was already expanded for deeper ones -
        without any Redis round-trip. A branch only gets (re)materialized
        into the wx.TreeCtrl if _branch_relevant says it's worth showing,
        so this never force-renders a branch nobody will end up seeing."""
        if self._hidden_root is None:
            return
        expanded_ids = self._expanded_node_ids
        selection = self._tree.GetSelection()
        selected_ref = self._tree.GetItemData(selection) if selection.IsOk() else None
        selected_node = selected_ref.node if selected_ref is not None else None

        self._tree.DeleteChildren(self._hidden_root)
        self._top_level_items = {}
        for label in self._top_level_order:
            node = self._root["children"][label]
            if not self._branch_relevant(label, node):
                continue
            label_text = f"{label} ({len(node['leaves'])})" if node["leaves"] else label
            item = self._tree.AppendItem(self._hidden_root, label_text)
            self._tree.SetItemData(item, _BranchRef(label, node))
            self._top_level_items[label] = item
            if node["children"]:
                if id(node) in expanded_ids:
                    self._add_children(item, node, label, expanded_ids)
                    self._tree.Expand(item)
                else:
                    self._tree.AppendItem(item, "")  # dummy placeholder for lazy expansion

        restored = None
        if selected_node is not None:
            restored = self._find_item_for_node(self._hidden_root, selected_node)
        if restored is not None:
            self._tree.SelectItem(restored)  # fires EVT_TREE_SEL_CHANGED -> re-filters the list
        else:
            self._list.set_keys([])
            self._delete_btn.Enable(False)

    def _collect_subtree_node_ids(self, node: Dict) -> Set[int]:
        """id(node) for `node` and every descendant in the in-memory trie
        - used to purge _expanded_node_ids of a branch being deleted, so a
        stale id can never coincidentally collide with some unrelated
        future node's id() once the deleted node is garbage collected."""
        ids = {id(node)}
        for child in node["children"].values():
            ids |= self._collect_subtree_node_ids(child)
        return ids

    def _remove_branch(self, path: str) -> None:
        """Drop `path` (and everything under it) from the tree - both the
        in-memory trie (self._root) and, if it's currently rendered, its
        wx tree item - after its keys have already been deleted from
        Redis. Purely local bookkeeping, no Redis round-trip."""
        segments = path.split(":")
        parent = self._root
        for segment in segments[:-1]:
            child = parent["children"].get(segment)
            if child is None:
                return  # already gone somehow - nothing left to do
            parent = child
        last = segments[-1]
        node = parent["children"].get(last)
        if node is None:
            return

        self._expanded_node_ids -= self._collect_subtree_node_ids(node)

        item = self._find_item_for_node(self._hidden_root, node) if self._hidden_root is not None else None
        if item is not None:
            self._tree.Delete(item)

        del parent["children"][last]
        if len(segments) == 1:
            self._top_level_order.remove(last)
            self._top_level_labels.discard(last)
            self._top_level_items.pop(last, None)

    def _on_delete(self, event: wx.CommandEvent) -> None:
        if self._datasource is None:
            return
        datasource = self._datasource
        selection = self._tree.GetSelection()
        if not selection.IsOk():
            return
        ref = self._tree.GetItemData(selection)
        if ref is None:
            return
        path = ref.path

        if path == NO_PREFIX_LABEL:
            # Synthetic bucket for keys with no ":" in their name at all -
            # they share no common substring a single Redis glob could
            # express, so this deletes exactly the key names already
            # known from the scan rather than a live pattern match.
            keys = sorted(ref.node["leaves"])
            if not keys:
                return
            noun = "key" if len(keys) == 1 else "keys"
            prompt = (
                f"Delete {len(keys):,} {noun} with no \":\" in their name?\n\n"
                "These share no common pattern, so they'll be deleted by exact name, "
                "not by a wildcard match."
            )
            work: Callable[[], int] = lambda: self._repository.delete_keys(datasource, keys)
        else:
            pattern = f"{path}:*"
            prompt = f'Delete every key matching "{pattern}"?\n\nThis cannot be undone.'
            work = lambda: self._repository.delete_keys_by_pattern(datasource, pattern)

        confirm = wx.MessageBox(prompt, "Delete keys", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        def on_success(_deleted_count: int) -> None:
            # Removed locally instead of re-scanning: reload()/rescan would
            # run through this same AsyncTaskRunner from inside this very
            # callback, whose "busy" flag isn't cleared until after this
            # callback returns - that call would be silently dropped (see
            # the same fix applied to IndexesView._on_delete), leaving the
            # just-deleted branch showing until the user hit Refresh.
            self._remove_branch(path)
            self._list.set_keys([])
            self._delete_btn.Enable(False)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f"Could not delete keys:\n\n{exc}",
                "Delete keys failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=work,
            on_success=on_success,
            on_error=on_error,
            disable=[self.refresh_btn, self._delete_btn],
        )


class KeySearchView(wx.Panel):
    """Search box (Redis glob pattern, e.g. "doc:foo:*") plus an optional
    type filter. Pattern-only searches filter the key list already cached
    from the Tree tab's scan - instant, no Redis round-trip. That cache
    fills in progressively as the scan runs (see begin_scan/add_keys), so
    a pattern-only search already returns partial (growing) results while
    a slow/large-keyspace scan is still in flight, flagged with "
    (scanning...)" until it finishes - and, once finished, it won't show a
    key created after that scan until `refresh_btn` (wired by
    DataExplorerPage to the same scan_keys() call, shared with the Tree
    tab) is used to pull a fresh one. Turning on a type filter switches to
    a live, debounced SCAN ... TYPE ... call instead, since key types
    aren't captured by that initial scan (see
    DatasourceRepository.search_keys) - that path already hits Redis on
    every query, so it sees new keys immediately without needing Refresh."""

    TYPE_CHOICES = ["Any type", "string", "hash", "list", "set", "zset", "stream"]
    DEBOUNCE_MS = 250

    def __init__(
        self,
        parent: wx.Window,
        repository: DatasourceRepository,
        on_activate_key: Optional[Callable[[str], None]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._datasource: Optional[Datasource] = None
        self._cached_keys: List[str] = []
        self._cached_truncated = False
        self._scanning = False
        self._on_refresh = on_refresh or (lambda: None)
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
        toolbar.Add(self._type_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self.refresh_btn = wx.Button(self, label="Refresh")
        self.refresh_btn.SetToolTip(
            "Re-scan the keyspace - picks up keys created since the cached list was last built "
            "(only affects pattern-only searches; a type filter always searches live)."
        )
        toolbar.Add(self.refresh_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 12)

        self._status = wx.StaticText(self, label="")
        sizer.Add(self._status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = KeyListCtrl(self, on_activate_key=on_activate_key)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(sizer)

        self._pattern_ctrl.Bind(wx.EVT_TEXT, self._on_query_changed)
        self._pattern_ctrl.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_cancel)
        self._type_choice.Bind(wx.EVT_CHOICE, self._on_query_changed)
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda event: self._on_refresh())
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)

    def set_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource

    def begin_scan(self) -> None:
        """Reset the pattern-only local-filter cache for a fresh scan -
        call once before the first add_keys() of a (re)scan."""
        self._cached_keys = []
        self._cached_truncated = False
        self._scanning = True
        if self._selected_type() is None:
            self._run_search()

    def add_keys(self, new_keys: List[str]) -> None:
        """Grow the pattern-only local-filter cache with one batch of
        newly-scanned keys, so a pattern-only search already reflects
        partial results while the scan is still in flight."""
        self._cached_keys.extend(new_keys)
        if self._selected_type() is None:
            self._run_search()

    def finish_scan(self, truncated: bool) -> None:
        """Called once the Tree tab's scan completes."""
        self._cached_truncated = truncated
        self._scanning = False
        if self._selected_type() is None:
            self._run_search()

    def clear(self) -> None:
        self._timer.Stop()
        self._pattern_ctrl.SetValue("")
        self._type_choice.SetSelection(0)
        self._list.set_keys([])
        self._status.SetLabel("")
        self._cached_keys = []
        self._cached_truncated = False
        self._scanning = False

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
            if self._cached_truncated:
                suffix = " (tree scan was truncated - results may be incomplete)"
            elif self._scanning:
                suffix = " (scanning...)"
            else:
                suffix = ""
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
        self._scanned_count = 0

        sizer = wx.BoxSizer(wx.VERTICAL)

        self._title = wx.StaticText(self, label="Data Explorer")
        font = self._title.GetFont()
        font.MakeBold()
        self._title.SetFont(font)
        sizer.Add(self._title, 0, wx.ALL, 12)

        notebook = wx.Notebook(self)
        self._tree_view = KeyTreeView(
            notebook, repository, on_activate_key=self._on_key_activated, on_refresh=self._rescan_keys
        )
        notebook.AddPage(self._tree_view, "Tree")
        self._search_view = KeySearchView(
            notebook, repository, on_activate_key=self._on_key_activated, on_refresh=self._rescan_keys
        )
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
        self._tree_view.set_datasource(datasource)
        self._search_view.clear()
        self._search_view.set_datasource(datasource)
        self._scripts_view.clear()
        self._scripts_view.set_datasource(datasource)
        self._indexes_view.clear()
        self._indexes_view.set_datasource(datasource)
        self._stats_view.clear()
        self._stats_view.set_datasource(datasource)
        self._rescan_keys()

    def _rescan_keys(self) -> None:
        """Run (or re-run) the keyspace scan and push results to both the
        Tree and Search tabs - the same bounded, background-thread SCAN
        used once at connect time by open_datasource, just made
        re-triggerable via either tab's Refresh button. Keeping this as
        one shared scan (rather than each tab re-scanning independently)
        means Tree and Search never disagree about what currently exists,
        and Refresh costs exactly what the initial connect-time scan
        already cost - no new always-on polling or per-keystroke cost is
        introduced.

        Results are pushed to both tabs progressively as each SCAN batch
        comes back (see scan_keys' on_progress), via begin_scan()/
        add_keys(), rather than only once the whole scan finishes - so a
        slow/large-keyspace scan leaves the tree and pattern-only search
        browsable almost immediately instead of blocking the UI on the
        whole thing."""
        if self._datasource is None:
            return
        datasource = self._datasource
        self._on_status("Scanning keys... 0")
        self._scanned_count = 0
        self._tree_view.begin_scan()
        self._search_view.begin_scan()

        def progress(new_keys: List[str]) -> None:
            wx.CallAfter(self._on_scan_progress, new_keys)

        def on_success(result) -> None:
            suffix = " (truncated)" if result.truncated else ""
            self._search_view.finish_scan(result.truncated)
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
            disable=[self._tree_view.refresh_btn, self._search_view.refresh_btn],
        )

    def _on_scan_progress(self, new_keys: List[str]) -> None:
        """UI-thread handler for one scan_keys() batch - feeds it to both
        tabs and updates the running count in the status area."""
        self._scanned_count += len(new_keys)
        self._tree_view.add_keys(new_keys)
        self._search_view.add_keys(new_keys)
        self._on_status(f"Scanning keys... {self._scanned_count:,}")
