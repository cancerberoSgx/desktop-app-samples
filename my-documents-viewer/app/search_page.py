from pathlib import Path
from typing import List

import wx

from .async_task import AsyncTaskRunner
from .document_viewer import DocumentViewerPanel
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
    group_by_document) rather than one row per matching chunk - selecting a
    document loads its full text into the right-hand DocumentViewerPanel,
    which highlights every matching chunk and offers a table of contents to
    jump between them."""

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

        results_splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        results_splitter.SetMinimumPaneSize(200)

        self._list = wx.ListCtrl(results_splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Score", width=70)
        self._list.InsertColumn(1, "Document", width=260)
        self._list.InsertColumn(2, "Matches", width=60)
        self._list.InsertColumn(3, "Best snippet", width=300)

        self._viewer = DocumentViewerPanel(results_splitter)

        results_splitter.SplitVertically(self._list, self._viewer, 500)
        outer.Add(results_splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._status_label = wx.StaticText(self, label=self._initial_status())
        outer.Add(self._status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._search_btn.Bind(wx.EVT_BUTTON, self._on_search)
        self._query_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_result_selected)

    def _initial_status(self) -> str:
        if self._repository.vector_enabled:
            return "Vector search is available."
        return "Vector search is unavailable on this build - only full-text search will run."

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self._list.DeleteAllItems()
        self._results = []
        self._viewer.clear()
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
            self._viewer.clear()
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

    def _on_result_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if index < 0 or index >= len(self._results):
            return
        self._load_and_show(self._results[index])

    def _load_and_show(self, doc: DocumentSearchResult) -> None:
        self._viewer.show_loading(doc.document_path)

        def on_success(text: str) -> None:
            self._viewer.show_document(doc.document_path, text, doc.matches, initial_index=doc.best_index)

        def on_error(exc: Exception) -> None:
            self._viewer.show_error(doc.document_path, str(exc))

        self._viewer_async.run(
            work=lambda: extract_text(Path(doc.document_path)),
            on_success=on_success,
            on_error=on_error,
        )
