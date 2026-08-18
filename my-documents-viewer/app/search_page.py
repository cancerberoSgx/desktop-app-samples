from pathlib import Path
from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .document_viewer import DocumentViewerFrame
from .models import DocumentSearchResult
from .repositories import DocumentRepository, ProfileRepository, group_by_document
from .text_extract import extract_text

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
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._profile_repository = profile_repository
        self._profile_id = profile_id
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

    def _initial_status(self) -> str:
        if self._repository.vector_enabled:
            return "Vector search is available."
        return "Vector search is unavailable on this build - only full-text search will run."

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self._list.DeleteAllItems()
        self._results = []
        self._status_label.SetLabel(self._initial_status())

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
                self._list.SetItem(row, 1, doc.document_path)
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
        viewer = self._get_viewer_frame()
        viewer.show_loading(doc.document_path)
        viewer.Show()
        viewer.Raise()

        def on_success(text: str) -> None:
            viewer.show_document(doc.document_path, text, doc.matches)

        def on_error(exc: Exception) -> None:
            viewer.show_error(doc.document_path, str(exc))

        self._viewer_async.run(
            work=lambda: extract_text(Path(doc.document_path)),
            on_success=on_success,
            on_error=on_error,
        )
