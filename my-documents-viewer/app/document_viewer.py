from typing import List, Optional

import wx
import wx.stc as stc

from .models import SearchResult

# Indicators (Scintilla's mechanism for painting extra highlighting under/
# over text without touching the document's actual styling) - one per match
# "source", so a user can tell a full-text hit from a vector-only one at a
# glance, plus a third one to mark whichever match is currently active.
# Indicators only ever paint over actual characters, never spill onto blank
# lines/whitespace the way a native text selection would - see _activate(),
# which deliberately avoids SetSelection() for exactly that reason.
INDICATOR_FULLTEXT = 0
INDICATOR_VECTOR_ONLY = 1
INDICATOR_ACTIVE = 2

TOC_PREVIEW_LENGTH = 70


class DocumentViewerFrame(wx.Frame):
    """A separate top-level window that previews one document's full text
    with its matching chunks highlighted - opened by SearchPage when a
    result is double-clicked/activated. SearchPage reuses one instance
    across documents (recreating it if the user closes it) rather than
    piling up a new window per result clicked."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title="Document Viewer", size=(960, 720))
        self.panel = DocumentViewerPanel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        # Deferred: centering only works once the window is actually shown,
        # and this frame is constructed just before that happens.
        wx.CallAfter(self.CentreOnParent)

    def show_loading(self, document_path: str) -> None:
        self.SetTitle(f"Loading - {document_path}")
        self.panel.show_loading(document_path)

    def show_document(
        self, document_path: str, text: str, matches: List[SearchResult], properties: Optional[dict] = None
    ) -> None:
        self.SetTitle(document_path)
        self.panel.show_document(document_path, text, matches, properties=properties)

    def show_error(self, document_path: str, message: str) -> None:
        self.SetTitle(document_path)
        self.panel.show_error(document_path, message)


class DocumentViewerPanel(wx.Panel):
    """A document's full text, with every matching chunk highlighted and a
    virtual table of contents (one entry per chunk, sorted by score - the
    best match first) to jump between them. Hosted inside
    DocumentViewerFrame, a separate window opened from the Search page.

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

        self._splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self._splitter.SetMinimumPaneSize(150)
        self._last_sash = 320

        self._toc = wx.ListCtrl(self._splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._toc.InsertColumn(0, "Score", width=70)
        self._toc.InsertColumn(1, "Match", width=170)
        self._toc.InsertColumn(2, "Source", width=70)

        # A separate control from _toc (not a repurposed one) - _on_toc_select
        # assumes every selection means "scroll the STC to this offset",
        # which doesn't apply to a property row, and this widget's splitter
        # state already has some delicate edges (see _set_left_pane).
        self._properties_list = wx.ListCtrl(self._splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._properties_list.InsertColumn(0, "Key", width=140)
        self._properties_list.InsertColumn(1, "Value", width=180)
        self._properties_list.Hide()

        self._stc = stc.StyledTextCtrl(self._splitter, style=wx.BORDER_SUNKEN)
        self._configure_stc()

        self._splitter.SplitVertically(self._toc, self._stc, self._last_sash)
        outer.Add(self._splitter, 1, wx.EXPAND)

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

        # Outline only (no fill) - the active match already has a fill from
        # one of the two indicators above; this just marks which one.
        self._stc.IndicatorSetStyle(INDICATOR_ACTIVE, stc.STC_INDIC_BOX)
        self._stc.IndicatorSetForeground(INDICATOR_ACTIVE, wx.Colour(230, 90, 0))

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
        self._properties_list.DeleteAllItems()
        self._set_left_pane(None)
        self._set_text("")
        self._enable_nav(False)

    def show_loading(self, document_path: str) -> None:
        self._matches = []
        self._active_index = -1
        self._path_label.SetLabel(f"Loading {document_path}...")
        self._match_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._properties_list.DeleteAllItems()
        self._set_left_pane(None)
        self._set_text("")
        self._enable_nav(False)

    def show_error(self, document_path: str, message: str) -> None:
        self._matches = []
        self._active_index = -1
        self._path_label.SetLabel(document_path)
        self._match_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._properties_list.DeleteAllItems()
        self._set_left_pane(None)
        self._set_text(f"Could not open this document:\n\n{message}")
        self._enable_nav(False)

    def show_document(
        self,
        document_path: str,
        text: str,
        matches: List[SearchResult],
        properties: Optional[dict] = None,
    ) -> None:
        """`matches` should already be sorted by score descending (see
        DocumentSearchResult.matches) - the order the table of contents and
        prev/next navigation use, so the best-scoring chunk opens first.

        An empty `matches` list means "plain content view" (e.g. opened from
        the Documents page rather than a search result); if `properties` is
        also given (a record/container's raw field values - see
        DocumentRepository.get_content's callers), a read-only Properties
        list takes the table of contents' place in the left pane instead of
        it being hidden outright."""
        self._matches = matches
        self._path_label.SetLabel(document_path)
        # Resize the STC (splitting/unsplitting the left pane) before loading
        # the text into it, not after - word wrap recalculates on resize,
        # and doing it the other way round briefly wraps the full text at
        # the old (narrower or wider) width first.
        if matches:
            self._set_left_pane(self._toc)
        elif properties:
            self._set_left_pane(self._properties_list)
        else:
            self._set_left_pane(None)
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
            self._toc.InsertItem(row, f"{match.score:.4f}")
            self._toc.SetItem(row, 1, preview)
            self._toc.SetItem(row, 2, _source_label(match))

        self._properties_list.DeleteAllItems()
        for row, (key, value) in enumerate((properties or {}).items()):
            self._properties_list.InsertItem(row, str(key))
            self._properties_list.SetItem(row, 1, str(value))

        self._paint_indicators(text, matches)
        self._enable_nav(bool(matches))
        if matches:
            self._activate(0)

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

    def _set_left_pane(self, widget: Optional[wx.Window]) -> None:
        """Show/hide the splitter's left pane - the table of contents when
        there are search matches, a read-only Properties list when viewing a
        record/container with none, or nothing at all (a plain file view -
        there's nothing useful to show, so it's unsplit entirely rather than
        shown empty). `widget` is `self._toc`, `self._properties_list`, or
        None."""
        self._match_label.Show(widget is self._toc)
        self._prev_btn.Show(widget is self._toc)
        self._next_btn.Show(widget is self._toc)

        if self._splitter.IsSplit():
            self._last_sash = self._splitter.GetSashPosition()
            self._splitter.Unsplit(self._splitter.GetWindow1())
        self._toc.Hide()
        self._properties_list.Hide()

        if widget is not None:
            widget.Show()
            self._splitter.SplitVertically(widget, self._stc, self._last_sash)

        self.Layout()
        # Unsplit()/SplitVertically() can leave stale pixels behind where the
        # sash/other pane used to be until the next natural repaint - force
        # one so the switch is clean immediately.
        self._splitter.Refresh()
        self._stc.Refresh()

    def _enable_nav(self, has_matches: bool) -> None:
        self._prev_btn.Enable(has_matches and self._active_index > 0)
        self._next_btn.Enable(has_matches and self._active_index < len(self._matches) - 1)

    def _paint_indicators(self, text: str, matches: List[SearchResult]) -> None:
        length = self._stc.GetTextLength()
        for indicator in (INDICATOR_FULLTEXT, INDICATOR_VECTOR_ONLY, INDICATOR_ACTIVE):
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

        # Deliberately not a native text selection (SetSelection) - that
        # paints a solid bar across whatever falls in [start, end), including
        # blank lines with no chunk text on them, which reads as unrelated
        # text being highlighted. An indicator only ever paints under actual
        # characters, so it can't bleed onto blank space that way.
        self._stc.SetIndicatorCurrent(INDICATOR_ACTIVE)
        self._stc.IndicatorClearRange(0, self._stc.GetTextLength())
        if end > start:
            self._stc.IndicatorFillRange(start, end - start)

        self._stc.GotoPos(start)
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
