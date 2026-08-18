from typing import List

import wx

from .async_task import AsyncTaskRunner
from .models import SearchResult
from .repositories import DocumentRepository, ProfileRepository

MODE_LABELS = [
    ("hybrid", "Hybrid (full-text + vector)"),
    ("fulltext", "Full-text only"),
    ("vector", "Vector only"),
]


class SearchPage(wx.Panel):
    """Search the active profile's indexed documents: hybrid (full-text +
    vector, blended via Reciprocal Rank Fusion), full-text-only, or
    vector-only."""

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
        self._results: List[SearchResult] = []
        self._async = AsyncTaskRunner(self)

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
        self._list.InsertColumn(2, "Snippet", width=460)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._status_label = wx.StaticText(self, label=self._initial_status())
        outer.Add(self._status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._search_btn.Bind(wx.EVT_BUTTON, self._on_search)
        self._query_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_open_selected)

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

        def on_success(results: List[SearchResult]) -> None:
            self._results = results
            self._list.DeleteAllItems()
            for row, result in enumerate(results):
                self._list.InsertItem(row, f"{result.score:.4f}")
                self._list.SetItem(row, 1, result.document_path)
                self._list.SetItem(row, 2, result.snippet)
            self._status_label.SetLabel(
                f"{len(results)} result(s) for \"{query}\" ({mode})" if results
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

    def _on_open_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if index < 0 or index >= len(self._results):
            return
        result = self._results[index]
        wx.MessageBox(
            f"{result.document_path}\n\n{result.snippet}",
            "Result",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
