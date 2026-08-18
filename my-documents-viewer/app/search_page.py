from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .document_viewer import DocumentViewerFrame
from .file_display import FILE_NAME_DISPLAY_DEFAULT, format_document_label
from .list_ctrl_utils import bind_hover_path_tooltip
from .models import KIND_CONTAINER, KIND_RECORD, DocumentSearchResult
from .repositories import DocumentRepository, ProfileRepository, group_by_document

MODE_LABELS = [
    ("hybrid", "Hybrid (full-text + vector)"),
    ("fulltext", "Full-text only"),
    ("vector", "Vector only"),
]


class SearchPage(wx.Panel):
    """Search the active profile's indexed documents: hybrid (full-text +
    vector, blended via Reciprocal Rank Fusion), full-text-only, or
    vector-only.

    Results are grouped one row per document (see repositories.
    group_by_document) rather than one row per matching chunk - double-
    clicking (or pressing Enter on) a result opens its full text in a
    separate DocumentViewerFrame window, which highlights every matching
    chunk and offers a table of contents, sorted by score, to jump between
    them."""

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
        self._results: List[DocumentSearchResult] = []
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

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Score", width=80)
        self._list.InsertColumn(1, "Document", width=340)
        self._list.InsertColumn(2, "Matches", width=70)
        self._list.InsertColumn(3, "Best snippet", width=380)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._status_label = wx.StaticText(self, label=self._initial_status())
        outer.Add(self._status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._search_btn.Bind(wx.EVT_BUTTON, self._on_search)
        self._query_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_result_activated)
        bind_hover_path_tooltip(
            self._list, lambda row: self._results[row].document_path if 0 <= row < len(self._results) else None
        )

    def _initial_status(self) -> str:
        if self._repository.vector_enabled:
            return "Vector search is available."
        return "Vector search is unavailable on this build - only full-text search will run."

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self._list.DeleteAllItems()
        self._results = []
        self._status_label.SetLabel(self._initial_status())

    def set_file_name_display(self, mode: str) -> None:
        self._file_name_display = mode
        for row, doc in enumerate(self._results):
            self._list.SetItem(row, 1, self._label_for(doc.document_id))

    def _label_for(self, document_id: int) -> str:
        """A record's DocumentSearchResult.document_path is a synthetic
        string DocumentRepository generates purely to keep it unique (see
        migration 0006) - never meant to be shown, so results are labeled
        via the real Document (and, for a record, its container) instead."""
        document = self._repository.get(document_id)
        if document is None:
            return "(removed)"
        container = self._repository.get(document.parent_document_id) if document.parent_document_id else None
        return format_document_label(document, container, self._file_name_display)

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
            self._results = group_by_document(results)
            self._list.DeleteAllItems()
            for row, doc in enumerate(self._results):
                self._list.InsertItem(row, f"{doc.score:.4f}")
                self._list.SetItem(row, 1, self._label_for(doc.document_id))
                self._list.SetItem(row, 2, str(len(doc.matches)))
                self._list.SetItem(row, 3, doc.best_match.snippet)
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

    def _on_result_activated(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if index < 0 or index >= len(self._results):
            return
        self._load_and_show(self._results[index])

    def _get_viewer_frame(self) -> DocumentViewerFrame:
        if self._viewer_frame is None:
            self._viewer_frame = DocumentViewerFrame(self)
            self._viewer_frame.Bind(wx.EVT_CLOSE, self._on_viewer_closed)
        return self._viewer_frame

    def _on_viewer_closed(self, event: wx.CloseEvent) -> None:
        self._viewer_frame = None
        event.Skip()

    def _load_and_show(self, doc: DocumentSearchResult) -> None:
        label = self._label_for(doc.document_id)
        document = self._repository.get(doc.document_id)
        properties = document.properties if document and document.kind in (KIND_RECORD, KIND_CONTAINER) else None

        viewer = self._get_viewer_frame()
        # Show/Raise before feeding it content - the viewer's splitter can
        # leave stale rendering behind if its split state changes before the
        # top-level window has ever been mapped (a GTK realization quirk).
        viewer.Show()
        viewer.Raise()
        viewer.show_loading(label)

        def on_success(text: str) -> None:
            viewer.show_document(label, text, doc.matches, properties=properties)

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
