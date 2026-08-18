from typing import List, Optional

import wx
import wx.stc as stc

from .models import SearchResult

# Indicators (Scintilla's mechanism for painting extra highlighting under/
# over text without touching the document's actual styling) - one per match
# "source", so a user can tell a full-text hit from a vector-only one at a
# glance, plus how many total.
INDICATOR_FULLTEXT = 0
INDICATOR_VECTOR_ONLY = 1

TOC_PREVIEW_LENGTH = 70


class DocumentViewerPanel(wx.Panel):
    """Right-hand pane of the Search page: the full text of one matched
    document, with every matching chunk highlighted and a virtual table of
    contents (one entry per chunk, in reading order) to jump between them.

    Chunk spans come from SearchResult.start_offset/end_offset - character
    offsets into the document's extracted text, recorded at index time (see
    chunking.chunk_text). Scintilla positions are byte offsets into the
    UTF-8 buffer, not character offsets, so every offset is converted via
    _char_to_byte() before being handed to the control.
    """

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._matches: List[SearchResult] = []
        self._active_index: int = -1

        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        self._path_label = wx.StaticText(self, label="Select a search result to preview it here.")
        header.Add(self._path_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._match_label = wx.StaticText(self, label="")
        header.Add(self._match_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._prev_btn = wx.Button(self, label="◀ Prev", style=wx.BU_EXACTFIT)
        self._next_btn = wx.Button(self, label="Next ▶", style=wx.BU_EXACTFIT)
        header.Add(self._prev_btn, 0, wx.RIGHT, 4)
        header.Add(self._next_btn, 0)
        outer.Add(header, 0, wx.EXPAND | wx.ALL, 8)

        self._warning_label = wx.StaticText(self, label="")
        self._warning_label.SetForegroundColour(wx.Colour(170, 100, 0))
        outer.Add(self._warning_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self._warning_label.Hide()

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        splitter.SetMinimumPaneSize(150)

        self._toc = wx.ListCtrl(splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._toc.InsertColumn(0, "Match", width=180)
        self._toc.InsertColumn(1, "Source", width=80)

        self._stc = stc.StyledTextCtrl(splitter, style=wx.BORDER_SUNKEN)
        self._configure_stc()

        splitter.SplitVertically(self._toc, self._stc, 260)
        outer.Add(splitter, 1, wx.EXPAND)

        self.SetSizer(outer)

        self._toc.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_toc_select)
        self._prev_btn.Bind(wx.EVT_BUTTON, lambda evt: self._activate(self._active_index - 1))
        self._next_btn.Bind(wx.EVT_BUTTON, lambda evt: self._activate(self._active_index + 1))

        self.clear()

    # ------------------------------------------------------------------
    # Scintilla setup
    # ------------------------------------------------------------------
    def _configure_stc(self) -> None:
        self._stc.SetReadOnly(True)
        self._stc.SetWrapMode(stc.STC_WRAP_WORD)
        self._stc.SetMarginType(0, stc.STC_MARGIN_NUMBER)
        self._stc.SetMarginWidth(0, 42)

        # Plain prose reads better in a proportional font than Scintilla's
        # default monospace look - this is a document viewer, not a code
        # editor.
        self._stc.StyleSetFont(stc.STC_STYLE_DEFAULT, wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self._stc.StyleClearAll()

        self._stc.IndicatorSetStyle(INDICATOR_FULLTEXT, stc.STC_INDIC_ROUNDBOX)
        self._stc.IndicatorSetForeground(INDICATOR_FULLTEXT, wx.Colour(240, 200, 0))
        self._stc.IndicatorSetAlpha(INDICATOR_FULLTEXT, 90)
        self._stc.IndicatorSetUnder(INDICATOR_FULLTEXT, True)

        self._stc.IndicatorSetStyle(INDICATOR_VECTOR_ONLY, stc.STC_INDIC_ROUNDBOX)
        self._stc.IndicatorSetForeground(INDICATOR_VECTOR_ONLY, wx.Colour(90, 170, 255))
        self._stc.IndicatorSetAlpha(INDICATOR_VECTOR_ONLY, 90)
        self._stc.IndicatorSetUnder(INDICATOR_VECTOR_ONLY, True)

    # ------------------------------------------------------------------
    # Public API - see SearchPage._load_and_show for the caller side
    # ------------------------------------------------------------------
    def clear(self) -> None:
        self._matches = []
        self._active_index = -1
        self._path_label.SetLabel("Select a search result to preview it here.")
        self._match_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._set_text("")
        self._enable_nav(False)

    def show_loading(self, document_path: str) -> None:
        self._matches = []
        self._active_index = -1
        self._path_label.SetLabel(f"Loading {document_path}...")
        self._match_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._set_text("")
        self._enable_nav(False)

    def show_error(self, document_path: str, message: str) -> None:
        self._matches = []
        self._active_index = -1
        self._path_label.SetLabel(document_path)
        self._match_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._set_text(f"Could not open this document:\n\n{message}")
        self._enable_nav(False)

    def show_document(
        self,
        document_path: str,
        text: str,
        matches: List[SearchResult],
        initial_index: int = 0,
    ) -> None:
        """`matches` should already be sorted by start_offset (see
        DocumentSearchResult.matches) - reading order for the table of
        contents. `initial_index` is which entry to jump to first (the
        overall best-scoring match)."""
        self._matches = matches
        self._path_label.SetLabel(document_path)
        self._set_text(text)

        clamped = any(match.end_offset > len(text) for match in matches)
        self._set_warning(
            "This document appears to have changed since it was indexed - "
            "highlighted positions may be off. Reindex it to refresh."
            if clamped
            else None
        )

        self._toc.DeleteAllItems()
        for row, match in enumerate(matches):
            preview = " ".join(match.snippet.split())[:TOC_PREVIEW_LENGTH]
            self._toc.InsertItem(row, preview)
            self._toc.SetItem(row, 1, _source_label(match))

        self._paint_indicators(text, matches)
        self._enable_nav(bool(matches))
        if matches:
            self._activate(initial_index)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _set_text(self, text: str) -> None:
        self._stc.SetReadOnly(False)
        self._stc.SetText(text)
        self._stc.SetReadOnly(True)
        self._stc.EmptyUndoBuffer()

    def _set_warning(self, message: Optional[str]) -> None:
        if message:
            self._warning_label.SetLabel(message)
            self._warning_label.Show()
        else:
            self._warning_label.Hide()
        self.Layout()

    def _enable_nav(self, has_matches: bool) -> None:
        self._prev_btn.Enable(has_matches and self._active_index > 0)
        self._next_btn.Enable(has_matches and self._active_index < len(self._matches) - 1)

    def _paint_indicators(self, text: str, matches: List[SearchResult]) -> None:
        length = self._stc.GetTextLength()
        for indicator in (INDICATOR_FULLTEXT, INDICATOR_VECTOR_ONLY):
            self._stc.SetIndicatorCurrent(indicator)
            self._stc.IndicatorClearRange(0, length)

        for match in matches:
            start = _char_to_byte(text, min(match.start_offset, len(text)))
            end = _char_to_byte(text, min(match.end_offset, len(text)))
            if end <= start:
                continue
            indicator = INDICATOR_VECTOR_ONLY if match.is_vector_only else INDICATOR_FULLTEXT
            self._stc.SetIndicatorCurrent(indicator)
            self._stc.IndicatorFillRange(start, end - start)

    def _activate(self, index: int) -> None:
        if not self._matches:
            return
        index = max(0, min(index, len(self._matches) - 1))
        self._active_index = index
        match = self._matches[index]

        self._match_label.SetLabel(f"Match {index + 1} of {len(self._matches)}")
        self._enable_nav(True)

        if self._toc.GetFirstSelected() != index:
            self._toc.Select(index)
        self._toc.EnsureVisible(index)

        text = self._stc.GetText()
        start = _char_to_byte(text, min(match.start_offset, len(text)))
        end = _char_to_byte(text, min(match.end_offset, len(text)))
        self._stc.SetSelection(start, end)
        self._stc.ScrollRange(start, end)
        self._stc.EnsureCaretVisible()

    def _on_toc_select(self, event: wx.ListEvent) -> None:
        self._activate(event.GetIndex())


def _char_to_byte(text: str, char_offset: int) -> int:
    """Scintilla positions are byte offsets into the UTF-8 buffer, but the
    offsets recorded on chunks are Python character offsets - identical for
    ASCII text but not once non-ASCII characters are involved. Cheap enough
    for the small number of offsets a document's matches produce."""
    return len(text[:char_offset].encode("utf-8"))


def _source_label(match: SearchResult) -> str:
    if match.fts_rank is not None and match.vector_rank is not None:
        return "Both"
    if match.vector_rank is not None:
        return "Vector"
    return "Full-text"
