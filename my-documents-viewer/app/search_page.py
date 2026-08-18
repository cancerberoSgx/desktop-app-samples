from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import wx
import wx.dataview as dv

from .async_task import AsyncTaskRunner
from .document_viewer import DocumentViewerFrame
from .file_display import FILE_NAME_DISPLAY_DEFAULT, format_document_label, format_record_short_label
from .models import KIND_RECORD, Document, DocumentSearchResult
from .repositories import DocumentRepository, ProfileRepository, group_by_document

MODE_LABELS = [
    ("hybrid", "Hybrid (full-text + vector)"),
    ("fulltext", "Full-text only"),
    ("vector", "Vector only"),
]


@dataclass
class SearchResultGroup:
    """One top-level row in the results tree - either a plain file that
    itself matched (`self_result` set, no `children`), or a container that
    has no content of its own but whose record children matched (`children`
    populated, `self_result` None). `score`/`match_count`/`best_snippet` are
    the group's own values for a file, or aggregated from its best-scoring
    child for a container - same "ordered by best chunk's score" philosophy
    repositories.group_by_document already uses for individual documents."""

    top_document: Document
    score: float
    match_count: int
    best_snippet: str
    self_result: Optional[DocumentSearchResult] = None
    children: List[Tuple[Document, DocumentSearchResult]] = field(default_factory=list)


class SearchPage(wx.Panel):
    """Search the active profile's indexed documents: hybrid (full-text +
    vector, blended via Reciprocal Rank Fusion), full-text-only, or
    vector-only.

    Results are shown as a tree (wx.dataview.TreeListCtrl), not a flat list:
    a plain file that matched is its own top-level row; a container whose
    records matched (see app/data_import.py) instead shows the container as
    an expandable top-level row, with each matching record nested under it
    as its own scored row (see SearchResultGroup/_build_result_groups) - the
    same parent/child shape DocumentsPage's own tree uses for browsing, just
    built eagerly from the (already small) result set rather than lazily
    from the database. Double-clicking (or pressing Enter on) a file or
    record row opens its full text in a separate DocumentViewerFrame window,
    which highlights every matching chunk and offers a table of contents,
    sorted by score, to jump between them; a container row has no content of
    its own to open - double-clicking it just expands/collapses it."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DocumentRepository,
        profile_repository: ProfileRepository,
        profile_id: int,
        file_name_display: str = FILE_NAME_DISPLAY_DEFAULT,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._profile_repository = profile_repository
        self._profile_id = profile_id
        self._file_name_display = file_name_display
        self._results: List[SearchResultGroup] = []
        self._async = AsyncTaskRunner(self)
        # Separate from `_async`: loading a document's full text for preview
        # can happen while a search itself might still be settling, and
        # AsyncTaskRunner only runs one job at a time per instance.
        self._viewer_async = AsyncTaskRunner(self)
        # Lazily created, reused across documents; cleared (see
        # _on_viewer_closed) if the user closes the window, so the next
        # activation opens a fresh one.
        self._viewer_frame: Optional[DocumentViewerFrame] = None

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Search"), 0, wx.ALL, 12)

        query_row = wx.BoxSizer(wx.HORIZONTAL)
        self._query_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        query_row.Add(self._query_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._search_btn = wx.Button(self, label="Search")
        query_row.Add(self._search_btn, 0)
        outer.Add(query_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._mode_radio = wx.RadioBox(
            self,
            label="Mode",
            choices=[label for _key, label in MODE_LABELS],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        outer.Add(self._mode_radio, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        if not self._repository.vector_enabled:
            self._mode_radio.EnableItem(2, False)  # "Vector only" (index per MODE_LABELS: hybrid=0, fulltext=1, vector=2)

        # Column 0 carries the tree's hierarchy (indentation/expand arrows -
        # TreeListCtrl always shows those on its first column), so "Document"
        # goes there instead of "Score" the way the old flat wx.ListCtrl had
        # it - same reordering DocumentsPage's own tree already settled on.
        self._tree = dv.TreeListCtrl(self, style=dv.TL_DEFAULT_STYLE | wx.BORDER_SUNKEN)
        self._tree.AppendColumn("Document", width=340)
        self._tree.AppendColumn("Score", width=80)
        self._tree.AppendColumn("Matches", width=70)
        self._tree.AppendColumn("Best snippet", width=380)
        outer.Add(self._tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._status_label = wx.StaticText(self, label=self._initial_status())
        outer.Add(self._status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._search_btn.Bind(wx.EVT_BUTTON, self._on_search)
        self._query_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self._tree.Bind(dv.EVT_TREELIST_ITEM_ACTIVATED, self._on_result_activated)

    def _initial_status(self) -> str:
        if self._repository.vector_enabled:
            return "Vector search is available."
        return "Vector search is unavailable on this build - only full-text search will run."

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self._tree.DeleteAllItems()
        self._results = []
        self._status_label.SetLabel(self._initial_status())

    def set_file_name_display(self, mode: str) -> None:
        self._file_name_display = mode
        self._populate_tree()

    def _selected_mode(self) -> str:
        return MODE_LABELS[self._mode_radio.GetSelection()][0]

    def _on_search(self, event: wx.CommandEvent) -> None:
        query = self._query_ctrl.GetValue().strip()
        if not query:
            return
        profile = self._profile_repository.get(self._profile_id)
        if profile is None:
            return
        mode = self._selected_mode()

        def on_success(results) -> None:
            self._results = self._build_result_groups(group_by_document(results))
            self._populate_tree()
            self._status_label.SetLabel(
                f"{len(self._results)} document(s), {len(results)} match(es) for \"{query}\" ({mode})"
                if results
                else f"No results for \"{query}\" ({mode})"
            )

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Search failed:\n\n{exc}", "Search error", wx.OK | wx.ICON_ERROR, self)
            self._status_label.SetLabel("Search failed.")

        self._status_label.SetLabel("Searching...")
        self._async.run(
            work=lambda: self._repository.hybrid_search(profile, query, mode=mode),
            on_success=on_success,
            on_error=on_error,
            disable=[self._search_btn],
        )

    # ------------------------------------------------------------------
    # Grouping - one row per document (see repositories.group_by_document)
    # folded a level further into "one row per top-level parent" (plain
    # files as themselves, records nested under their container).
    # ------------------------------------------------------------------
    def _build_result_groups(self, doc_results: List[DocumentSearchResult]) -> List[SearchResultGroup]:
        groups = {}  # top_document_id -> SearchResultGroup
        for doc_result in doc_results:
            document = self._repository.get(doc_result.document_id)
            if document is None:
                continue  # removed since the search ran

            if document.kind == KIND_RECORD and document.parent_document_id:
                top_id = document.parent_document_id
                group = groups.get(top_id)
                if group is None:
                    container = self._repository.get(top_id)
                    if container is None:
                        continue  # container removed since the search ran
                    group = SearchResultGroup(top_document=container, score=0.0, match_count=0, best_snippet="")
                    groups[top_id] = group
                group.children.append((document, doc_result))
            else:
                groups[doc_result.document_id] = SearchResultGroup(
                    top_document=document,
                    score=doc_result.score,
                    match_count=len(doc_result.matches),
                    best_snippet=doc_result.best_match.snippet,
                    self_result=doc_result,
                )

        for group in groups.values():
            if group.children:
                group.children.sort(key=lambda pair: pair[1].score, reverse=True)
                _best_document, best_result = group.children[0]
                group.score = best_result.score
                group.match_count = sum(len(doc_result.matches) for _doc, doc_result in group.children)
                group.best_snippet = best_result.best_match.snippet

        # Same overall ordering the old flat list had: best-scoring document
        # (now best-scoring group) first.
        return sorted(groups.values(), key=lambda g: g.score, reverse=True)

    def _populate_tree(self) -> None:
        self._tree.DeleteAllItems()
        root = self._tree.GetRootItem()
        for group in self._results:
            item = self._tree.AppendItem(root, "")
            self._tree.SetItemData(item, group)
            self._tree.SetItemText(item, 0, format_document_label(group.top_document, None, self._file_name_display))
            self._tree.SetItemText(item, 1, f"{group.score:.4f}")
            self._tree.SetItemText(item, 2, str(group.match_count))
            self._tree.SetItemText(item, 3, group.best_snippet)

            for record_document, doc_result in group.children:
                child_item = self._tree.AppendItem(item, "")
                self._tree.SetItemData(child_item, doc_result)
                self._tree.SetItemText(child_item, 0, format_record_short_label(record_document, group.top_document))
                self._tree.SetItemText(child_item, 1, f"{doc_result.score:.4f}")
                self._tree.SetItemText(child_item, 2, str(len(doc_result.matches)))
                self._tree.SetItemText(child_item, 3, doc_result.best_match.snippet)

    def _selected_item_data(self):
        item = self._tree.GetSelection()
        if not item.IsOk():
            return None
        return self._tree.GetItemData(item)

    def _on_result_activated(self, event: dv.TreeListEvent) -> None:
        data = self._selected_item_data()
        if isinstance(data, SearchResultGroup):
            # A container group has no content of its own to open - just
            # let the tree's own double-click expand/collapse happen.
            if data.self_result is not None:
                self._load_and_show(data.self_result)
        elif isinstance(data, DocumentSearchResult):
            self._load_and_show(data)

    def _get_viewer_frame(self) -> DocumentViewerFrame:
        if self._viewer_frame is None:
            self._viewer_frame = DocumentViewerFrame(self)
            self._viewer_frame.Bind(wx.EVT_CLOSE, self._on_viewer_closed)
        return self._viewer_frame

    def _on_viewer_closed(self, event: wx.CloseEvent) -> None:
        self._viewer_frame = None
        event.Skip()

    def _load_and_show(self, doc: DocumentSearchResult) -> None:
        # A record's DocumentSearchResult.document_path is a synthetic
        # string DocumentRepository generates purely to keep it unique (see
        # migration 0006) - never meant to be shown, so the viewer's title
        # and details are built from the real Document (and, for a record,
        # its container) instead.
        document = self._repository.get(doc.document_id)
        if document is None:
            return  # removed since the search ran
        container = self._repository.get(document.parent_document_id) if document.parent_document_id else None
        label = format_document_label(document, container, self._file_name_display)

        viewer = self._get_viewer_frame()
        # Show/Raise before feeding it content - the viewer's splitter can
        # leave stale rendering behind if its split state changes before the
        # top-level window has ever been mapped (a GTK realization quirk).
        viewer.Show()
        viewer.Raise()
        viewer.show_loading(label)

        def on_success(text: str) -> None:
            viewer.show_document(label, document, text, doc.matches, container=container)

        def on_error(exc: Exception) -> None:
            viewer.show_error(label, str(exc))

        # get_content() dispatches on the document's kind - a record's
        # `document_path` isn't a real file (see migration 0006), so this
        # must go through the repository rather than reading the path
        # directly the way plain-file viewing once did.
        self._viewer_async.run(
            work=lambda: self._repository.get_content(doc.document_id),
            on_success=on_success,
            on_error=on_error,
        )
