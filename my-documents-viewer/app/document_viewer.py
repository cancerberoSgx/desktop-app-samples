from typing import Callable, List, Optional, Tuple

import wx
import wx.dataview as dv
import wx.stc as stc

from .file_display import format_embedding_status, format_record_short_label, format_size
from .models import KIND_CONTAINER, KIND_RECORD, Document, DocumentSearchResult, SearchResult

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

        # Ctrl+F/F3/Shift+F3 work regardless of which child control has
        # focus (an accelerator table dispatches at the frame level, unlike
        # a plain EVT_KEY_DOWN binding on one widget) - this is the one
        # DocumentViewerFrame instance both DocumentsPage and SearchPage
        # reuse, so find-in-text is available from either.
        find_id, next_id, prev_id = wx.NewIdRef(), wx.NewIdRef(), wx.NewIdRef()
        self.Bind(wx.EVT_MENU, lambda evt: self.panel.toggle_find_bar(), id=find_id)
        self.Bind(wx.EVT_MENU, lambda evt: self.panel.find_next(), id=next_id)
        self.Bind(wx.EVT_MENU, lambda evt: self.panel.find_prev(), id=prev_id)
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    (wx.ACCEL_CTRL, ord("F"), find_id),
                    (wx.ACCEL_NORMAL, wx.WXK_F3, next_id),
                    (wx.ACCEL_SHIFT, wx.WXK_F3, prev_id),
                ]
            )
        )

    def show_loading(self, label: str) -> None:
        self.SetTitle(f"Loading - {label}")
        self.panel.show_loading(label)

    def show_document(
        self,
        label: str,
        document: Document,
        text: str,
        matches: List[SearchResult],
        container: Optional[Document] = None,
    ) -> None:
        self.SetTitle(label)
        self.panel.show_document(label, document, text, matches, container=container)

    def show_records(self, label: str, container: Document, records: List[Document]) -> None:
        self.SetTitle(label)
        self.panel.show_records(label, container, records)

    def show_container_results(
        self,
        label: str,
        container: Document,
        children: List[Tuple[Document, DocumentSearchResult]],
        on_child_selected: Callable[[Document, DocumentSearchResult], None],
    ) -> None:
        self.SetTitle(label)
        self.panel.show_container_results(label, container, children, on_child_selected)

    def show_container_child_loading(self, label: str) -> None:
        self.panel.show_container_child_loading(label)

    def show_container_child_content(
        self,
        label: str,
        document: Document,
        text: str,
        matches: List[SearchResult],
        container: Optional[Document] = None,
    ) -> None:
        self.panel.show_container_child_content(label, document, text, matches, container=container)

    def show_container_child_error(self, label: str, message: str) -> None:
        self.panel.show_container_child_error(label, message)

    def show_error(self, label: str, message: str) -> None:
        self.SetTitle(label)
        self.panel.show_error(label, message)


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
        self._showing_records_grid: bool = False
        # Search-result container browsing (show_container_results) - the
        # rows currently listed on the left, and the owner's (SearchPage's)
        # hook for "the user picked this child, go load its content" since
        # this panel never talks to a repository directly.
        self._children_rows: List[Tuple[Document, DocumentSearchResult]] = []
        self.on_child_selected: Optional[Callable[[Document, DocumentSearchResult], None]] = None

        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        self._path_label = wx.StaticText(self, label="Select a search result to preview it here.")
        header.Add(self._path_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._match_label = wx.StaticText(self, label="")
        header.Add(self._match_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._prev_btn = wx.Button(self, label="◀ Prev", style=wx.BU_EXACTFIT)
        self._next_btn = wx.Button(self, label="Next ▶", style=wx.BU_EXACTFIT)
        header.Add(self._prev_btn, 0, wx.RIGHT, 4)
        header.Add(self._next_btn, 0, wx.RIGHT, 8)
        self._find_toggle_btn = wx.BitmapButton(self, bitmap=wx.ArtProvider.GetBitmap(wx.ART_FIND, wx.ART_BUTTON, (16, 16)))
        self._find_toggle_btn.SetToolTip("Find in text (Ctrl+F)")
        header.Add(self._find_toggle_btn, 0)
        outer.Add(header, 0, wx.EXPAND | wx.ALL, 8)

        # Always visible regardless of mode (plain file / record / matches
        # from search / records grid) - see _build_details_text. Separate
        # from _properties_list below, which holds the document's *own*
        # imported fields (or is absent entirely for a plain file); this is
        # DocumentRepository-tracked metadata (path, indexed_at, chunk
        # count, embedding status) that every document has.
        self._details_label = wx.StaticText(self, label="")
        self._details_label.SetForegroundColour(wx.Colour(110, 110, 110))
        outer.Add(self._details_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._warning_label = wx.StaticText(self, label="")
        self._warning_label.SetForegroundColour(wx.Colour(170, 100, 0))
        outer.Add(self._warning_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self._warning_label.Hide()

        # Find bar - hidden until toggled (button above, or Ctrl+F/F3/
        # Shift+F3 via DocumentViewerFrame's accelerator table). Deliberately
        # not tied to the chunk-match Prev/Next above: this searches the raw
        # text itself and always continues from wherever the view currently
        # is (see _run_find), rather than resetting to the top of the
        # document - i.e. it respects the current scroll position instead of
        # fighting it.
        self._find_bar = wx.Panel(self)
        find_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._find_ctrl = wx.TextCtrl(self._find_bar, style=wx.TE_PROCESS_ENTER)
        find_sizer.Add(self._find_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._find_prev_btn = wx.Button(self._find_bar, label="◀", style=wx.BU_EXACTFIT)
        self._find_next_btn = wx.Button(self._find_bar, label="▶", style=wx.BU_EXACTFIT)
        find_sizer.Add(self._find_prev_btn, 0, wx.RIGHT, 2)
        find_sizer.Add(self._find_next_btn, 0, wx.RIGHT, 8)
        self._find_status_label = wx.StaticText(self._find_bar, label="", size=(90, -1))
        find_sizer.Add(self._find_status_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._find_close_btn = wx.Button(self._find_bar, label="✕", style=wx.BU_EXACTFIT)
        find_sizer.Add(self._find_close_btn, 0)
        self._find_bar.SetSizer(find_sizer)
        outer.Add(self._find_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self._find_bar.Hide()

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

        # show_container_results()'s left pane - one row per matching child
        # record, best-scoring first; selecting a row asks the owner (via
        # on_child_selected) to load and display that child's content in the
        # main pane, the same way _toc's rows jump to a chunk within a
        # single document.
        self._children_list = wx.ListCtrl(self._splitter, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._children_list.InsertColumn(0, "Score", width=70)
        self._children_list.InsertColumn(1, "Record", width=170)
        self._children_list.InsertColumn(2, "Best snippet", width=250)
        self._children_list.Hide()

        self._stc = stc.StyledTextCtrl(self._splitter, style=wx.BORDER_SUNKEN)
        self._configure_stc()

        self._splitter.SplitVertically(self._toc, self._stc, self._last_sash)
        outer.Add(self._splitter, 1, wx.EXPAND)

        # A container has no content/chunks of its own (see
        # DocumentRepository.get_content) - show_records() lists its child
        # records here instead, as a flat data grid, full-width in the same
        # spot the splitter above otherwise occupies (see _set_content_mode).
        self._records_grid = dv.TreeListCtrl(self, style=dv.TL_DEFAULT_STYLE | wx.BORDER_SUNKEN)
        outer.Add(self._records_grid, 1, wx.EXPAND)
        self._records_grid.Hide()

        self.SetSizer(outer)

        self._toc.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_toc_select)
        self._children_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_children_select)
        self._prev_btn.Bind(wx.EVT_BUTTON, lambda evt: self._activate(self._active_index - 1))
        self._next_btn.Bind(wx.EVT_BUTTON, lambda evt: self._activate(self._active_index + 1))
        self._find_toggle_btn.Bind(wx.EVT_BUTTON, lambda evt: self.toggle_find_bar())
        self._find_close_btn.Bind(wx.EVT_BUTTON, lambda evt: self._show_find_bar(False))
        self._find_ctrl.Bind(wx.EVT_TEXT_ENTER, lambda evt: self.find_next())
        self._find_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_find_key_down)
        self._find_prev_btn.Bind(wx.EVT_BUTTON, lambda evt: self.find_prev())
        self._find_next_btn.Bind(wx.EVT_BUTTON, lambda evt: self.find_next())

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
        self._children_rows = []
        self.on_child_selected = None
        self._path_label.SetLabel("Select a search result to preview it here.")
        self._match_label.SetLabel("")
        self._details_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._properties_list.DeleteAllItems()
        self._children_list.DeleteAllItems()
        self._set_content_mode("text")
        self._set_left_pane(None)
        self._set_text("")
        self._enable_nav(False)

    def show_loading(self, label: str) -> None:
        self._matches = []
        self._active_index = -1
        self._children_rows = []
        self.on_child_selected = None
        self._path_label.SetLabel(f"Loading {label}...")
        self._match_label.SetLabel("")
        self._details_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._properties_list.DeleteAllItems()
        self._children_list.DeleteAllItems()
        self._set_content_mode("text")
        self._set_left_pane(None)
        self._set_text("")
        self._enable_nav(False)

    def show_error(self, label: str, message: str) -> None:
        self._matches = []
        self._active_index = -1
        self._children_rows = []
        self.on_child_selected = None
        self._path_label.SetLabel(label)
        self._match_label.SetLabel("")
        self._details_label.SetLabel("")
        self._set_warning(None)
        self._toc.DeleteAllItems()
        self._properties_list.DeleteAllItems()
        self._children_list.DeleteAllItems()
        self._set_content_mode("text")
        self._set_left_pane(None)
        self._set_text(f"Could not open this document:\n\n{message}")
        self._enable_nav(False)

    def show_document(
        self,
        label: str,
        document: Document,
        text: str,
        matches: List[SearchResult],
        container: Optional[Document] = None,
    ) -> None:
        """`matches` should already be sorted by score descending (see
        DocumentSearchResult.matches) - the order the table of contents and
        prev/next navigation use, so the best-scoring chunk opens first.

        An empty `matches` list means "plain content view" (e.g. opened from
        the Documents page rather than a search result). `document` (and,
        for a record, its parent `container`) drive both the always-visible
        details line (see _build_details_text) and, for a record, the
        read-only Properties list of its raw imported fields - which takes
        the table of contents' place in the left pane when there are no
        matches to show there instead."""
        properties = document.properties if document.kind == KIND_RECORD else None

        self._matches = matches
        self._children_rows = []
        self.on_child_selected = None
        self._children_list.DeleteAllItems()
        self._set_content_mode("text")
        self._path_label.SetLabel(label)
        self._details_label.SetLabel(self._build_details_text(document, container))
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

    def show_records(self, label: str, container: Document, records: List[Document]) -> None:
        """Container double-click view (DocumentsPage) - a container has no
        content/chunks of its own (see DocumentRepository.get_content), so
        rather than an empty/placeholder text view, show its child records
        as a flat data grid: one row per record, one column per original
        field. Columns are the union of keys across every record's raw
        `properties` (see data_import.py/DocumentRepository.import_data_file
        for where those are captured on import), in the order first seen -
        i.e. the same column order the source CSV/JSON had."""
        self._matches = []
        self._active_index = -1
        self._children_rows = []
        self.on_child_selected = None
        self._children_list.DeleteAllItems()
        self._path_label.SetLabel(label)
        self._match_label.SetLabel("")
        self._details_label.SetLabel(self._build_details_text(container, None))
        self._set_warning(None)
        self._enable_nav(False)

        self._records_grid.DeleteAllItems()
        self._records_grid.ClearColumns()

        columns: List[str] = []
        seen = set()
        for record in records:
            for key in (record.properties or {}).keys():
                if key not in seen:
                    seen.add(key)
                    columns.append(key)

        self._records_grid.AppendColumn("#", width=50)
        for column in columns:
            self._records_grid.AppendColumn(column, width=150)

        root = self._records_grid.GetRootItem()
        for row, record in enumerate(records, start=1):
            item = self._records_grid.AppendItem(root, "")
            self._records_grid.SetItemText(item, 0, str(row))
            properties = record.properties or {}
            for col_index, column in enumerate(columns, start=1):
                self._records_grid.SetItemText(item, col_index, str(properties.get(column, "")))

        self._set_content_mode("records")

    def show_container_results(
        self,
        label: str,
        container: Document,
        children: List[Tuple[Document, DocumentSearchResult]],
        on_child_selected: Callable[[Document, DocumentSearchResult], None],
    ) -> None:
        """Search-result container view (SearchPage) - unlike show_records()
        (DocumentsPage's full, content-less record browser: every record,
        flat grid, no text), this is for the small set of *matching* records
        a search actually surfaced: the left pane lists them ranked best
        first (score + snippet, same ordering as SearchResultGroup.children),
        and selecting one loads and displays its full text with its own
        matching chunks highlighted - the container becomes a browsable
        "document" whose chapters are its matching children.

        Loading a child's content is asynchronous (SearchPage owns the
        repository call), hence `on_child_selected` rather than handing this
        method the content directly - selecting the first row below fires it
        immediately, so the best-scoring child loads by default the same way
        show_document() opens on its best-scoring match."""
        self.on_child_selected = on_child_selected
        self._children_rows = children
        self._path_label.SetLabel(label)
        self._details_label.SetLabel(self._build_details_text(container, None))
        self._set_warning(None)
        self._set_content_mode("text")

        self._children_list.DeleteAllItems()
        for row, (document, result) in enumerate(children):
            preview = " ".join(result.best_match.snippet.split())[:TOC_PREVIEW_LENGTH]
            self._children_list.InsertItem(row, f"{result.score:.4f}")
            self._children_list.SetItem(row, 1, format_record_short_label(document, container))
            self._children_list.SetItem(row, 2, preview)

        self._matches = []
        self._set_text("")
        self._set_left_pane(self._children_list)
        self._enable_nav(False)

        if children:
            # Fires _on_children_select -> on_child_selected, the same
            # Select()-drives-the-callback pattern _activate() already uses
            # for _toc - see there for why this doesn't recurse forever.
            self._children_list.Select(0)
            self._children_list.Focus(0)
            self._children_list.EnsureVisible(0)

    def show_container_child_loading(self, label: str) -> None:
        """Placeholder while SearchPage fetches the selected child's content
        - keeps the children list in place (unlike show_loading(), which
        unsplits the left pane entirely for the "nothing to show yet at
        all" case before any container/document has been picked)."""
        self._path_label.SetLabel(f"Loading {label}...")
        self._details_label.SetLabel("")
        self._matches = []
        self._set_text("")
        self._enable_nav(False)

    def show_container_child_content(
        self,
        label: str,
        document: Document,
        text: str,
        matches: List[SearchResult],
        container: Optional[Document] = None,
    ) -> None:
        """Fills the right-hand pane for the child currently selected in
        show_container_results()'s left list - everything show_document()
        does for a document's text/matches/highlighting, minus touching the
        left pane (the children list stays put; there's no per-child TOC to
        swap in)."""
        self._matches = matches
        self._path_label.SetLabel(label)
        self._details_label.SetLabel(self._build_details_text(document, container))
        self._set_text(text)

        clamped = any(match.end_offset > len(text) for match in matches)
        self._set_warning(
            "This document appears to have changed since it was indexed - "
            "highlighted positions may be off. Reindex it to refresh."
            if clamped
            else None
        )

        self._paint_indicators(text, matches)
        self._enable_nav(bool(matches))
        if matches:
            self._activate(0)
        else:
            self._match_label.SetLabel("")

    def show_container_child_error(self, label: str, message: str) -> None:
        self._matches = []
        self._path_label.SetLabel(label)
        self._details_label.SetLabel("")
        self._set_warning(None)
        self._set_text(f"Could not open this document:\n\n{message}")
        self._enable_nav(False)

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

    @staticmethod
    def _build_details_text(document: Document, container: Optional[Document]) -> str:
        """Always-visible metadata line(s) under the title - full path,
        indexed date, and other DocumentRepository-tracked details, for
        both show_document (file/record) and show_records (container).

        A record's own `path` is a synthetic string never meant to be shown
        (see migration 0006) - so for a record this shows the *parent*
        container's real path/indexed date instead, plus the record's own
        row_key (its "child key" within that container) and its own
        indexed-at/chunk/embedding status, which are specific to the record
        itself rather than inherited from the container."""
        if document.kind == KIND_RECORD and container is not None:
            return (
                f"Parent: {container.path}  (imported {container.indexed_at or 'never'})\n"
                f"Child key: {document.row_key or f'record {document.id}'}    "
                f"Indexed: {document.indexed_at or 'never'}    "
                f"Chunks: {document.chunk_count}    "
                f"Embedding: {format_embedding_status(document)}"
            )

        if document.kind == KIND_CONTAINER:
            properties = document.properties or {}
            return (
                f"Path: {document.path}\n"
                f"Indexed: {document.indexed_at or 'never'}    "
                f"Format: {properties.get('format', '?')}    "
                f"Records: {properties.get('row_count', 0)}"
            )

        return (
            f"Path: {document.path}\n"
            f"Indexed: {document.indexed_at or 'never'}    "
            f"Size: {format_size(document.size_bytes)}    "
            f"Chunks: {document.chunk_count}    "
            f"Embedding: {format_embedding_status(document)}"
        )

    def _set_content_mode(self, mode: str) -> None:
        """Switch the panel's main content area between the text viewer
        (Scintilla + its left pane, mode="text") and show_records()'s flat
        data grid (mode="records") - the two are mutually exclusive
        full-width uses of the same space, toggled with Show()/Hide()
        rather than re-parenting anything. Find-in-text only makes sense
        against real text, so the find bar/button are unavailable in
        "records" mode."""
        self._showing_records_grid = mode == "records"
        self._splitter.Show(mode == "text")
        self._records_grid.Show(mode == "records")
        self._find_toggle_btn.Enable(mode == "text")
        if self._showing_records_grid:
            self._show_find_bar(False)
        self.Layout()

    def _set_left_pane(self, widget: Optional[wx.Window]) -> None:
        """Show/hide the splitter's left pane - the table of contents when
        there are search matches, a read-only Properties list when viewing a
        record/container with none, or nothing at all (a plain file view -
        there's nothing useful to show, so it's unsplit entirely rather than
        shown empty). `widget` is `self._toc`, `self._properties_list`,
        `self._children_list`, or None. Prev/Next and the match label apply
        whenever matches are in play - both `_toc` (a single document's own
        chunks) and `_children_list` (a container's children, each of which
        shows its own chunk matches once selected - see
        show_container_child_content)."""
        self._match_label.Show(widget in (self._toc, self._children_list))
        self._prev_btn.Show(widget in (self._toc, self._children_list))
        self._next_btn.Show(widget in (self._toc, self._children_list))

        if self._splitter.IsSplit():
            self._last_sash = self._splitter.GetSashPosition()
            self._splitter.Unsplit(self._splitter.GetWindow1())
        self._toc.Hide()
        self._properties_list.Hide()
        self._children_list.Hide()

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

        # Only sync the TOC's own selection when it's actually the pane in
        # play - in show_container_child_content(), _matches belongs to
        # whichever child is currently selected in _children_list, and the
        # (hidden, possibly stale) _toc has nothing to do with it.
        if self._toc.IsShown():
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

    def _on_children_select(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if 0 <= index < len(self._children_rows) and self.on_child_selected:
            document, result = self._children_rows[index]
            self.on_child_selected(document, result)

    # ------------------------------------------------------------------
    # Find in text - Ctrl+F/the toolbar button toggle the bar, F3/Shift+F3
    # (wired at the frame level, see DocumentViewerFrame) or its Prev/Next
    # buttons step through occurrences. Unlike the chunk-match Prev/Next
    # above (which always jumps to a specific recorded chunk offset), this
    # always searches from wherever the view is currently positioned/
    # scrolled to (see _run_find) rather than resetting to the top of the
    # document on every search.
    # ------------------------------------------------------------------
    def toggle_find_bar(self) -> None:
        if self._showing_records_grid:
            return
        if self._find_bar.IsShown():
            # Already open - Ctrl+F again just refocuses/reselects the
            # search box, the way most editors treat a repeated shortcut,
            # rather than closing it.
            self._find_ctrl.SetFocus()
            self._find_ctrl.SelectAll()
        else:
            self._show_find_bar(True)

    def find_next(self) -> None:
        self._find_or_open(forward=True)

    def find_prev(self) -> None:
        self._find_or_open(forward=False)

    def _find_or_open(self, forward: bool) -> None:
        if self._showing_records_grid:
            return
        if not self._find_bar.IsShown():
            self._show_find_bar(True)
            return
        self._run_find(forward)

    def _show_find_bar(self, show: bool) -> None:
        self._find_bar.Show(show)
        self.Layout()
        if show:
            self._find_ctrl.SetFocus()
            self._find_ctrl.SelectAll()
        else:
            self._find_status_label.SetLabel("")

    def _on_find_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._show_find_bar(False)
            return
        event.Skip()

    def _run_find(self, forward: bool) -> None:
        """Search from the current selection/caret position - NOT from the
        top of the document - so repeated Next/Prev presses continue in the
        direction the user is already reading, and the view only ever
        scrolls as far as the next match actually requires. Wraps around
        (forward past the end -> restart from the top; backward past the
        start -> restart from the end) rather than dead-ending."""
        query = self._find_ctrl.GetValue()
        if not query:
            self._find_status_label.SetLabel("")
            return

        length = self._stc.GetTextLength()
        sel_start, sel_end = self._stc.GetSelection()

        if forward:
            anchor = sel_end if sel_end > sel_start else self._stc.GetCurrentPos()
            start, end = self._stc.FindText(anchor, length, query, 0)
            if start == -1 and anchor > 0:
                start, end = self._stc.FindText(0, anchor, query, 0)
        else:
            anchor = sel_start if sel_end > sel_start else self._stc.GetCurrentPos()
            start, end = self._stc.FindText(anchor, 0, query, 0)
            if start == -1 and anchor < length:
                start, end = self._stc.FindText(length, anchor, query, 0)

        if start == -1:
            self._find_status_label.SetLabel("Not found")
            return

        self._find_status_label.SetLabel("")
        self._stc.SetSelection(start, end)
        self._stc.ScrollRange(start, end)
        self._stc.EnsureCaretVisible()


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
