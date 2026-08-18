from pathlib import Path
from typing import Callable, List, Optional

import wx

from .async_task import AsyncTaskRunner
from .document_viewer import DocumentViewerFrame
from .file_display import FILE_NAME_DISPLAY_DEFAULT, format_display_path
from .list_ctrl_utils import bind_hover_path_tooltip
from .models import Document
from .repositories import DocumentRepository, IndexRunSummary, ProfileRepository
from .text_extract import extract_text

FILE_DIALOG_WILDCARD = "Text/Markdown files (*.txt;*.md)|*.txt;*.md|All files (*.*)|*.*"


class DocumentsPage(wx.Panel):
    """CRUD + indexing screen for a profile's documents: add individual
    files or whole folders (walked recursively for .txt/.md), reindex
    (single or all, e.g. after an embedding model change), and remove."""

    def __init__(
        self,
        parent: wx.Window,
        repository: DocumentRepository,
        profile_repository: ProfileRepository,
        profile_id: int,
        on_status: Optional[Callable[[str], None]] = None,
        file_name_display: str = FILE_NAME_DISPLAY_DEFAULT,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._profile_repository = profile_repository
        self._profile_id = profile_id
        self._documents: List[Document] = []
        self._on_status = on_status or (lambda text: None)
        self._file_name_display = file_name_display
        self._async = AsyncTaskRunner(self)
        # Separate from `_async`: opening the content viewer shouldn't be
        # blocked by (or block) an indexing run in flight on this page.
        self._viewer_async = AsyncTaskRunner(self)
        # Lazily created, reused across documents; cleared (see
        # _on_viewer_closed) if the user closes the window, so the next
        # double-click opens a fresh one.
        self._viewer_frame: Optional[DocumentViewerFrame] = None

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Documents"), 0, wx.ALL, 12)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._add_files_btn = wx.Button(self, label="Add Files...")
        self._add_folder_btn = wx.Button(self, label="Add Folder...")
        self._reindex_btn = wx.Button(self, label="Reindex Selected")
        self._reindex_all_btn = wx.Button(self, label="Reindex All")
        self._remove_btn = wx.Button(self, label="Remove")
        for btn in (
            self._add_files_btn,
            self._add_folder_btn,
            self._reindex_btn,
            self._reindex_all_btn,
            self._remove_btn,
        ):
            toolbar.Add(btn, 0, wx.RIGHT, 8)
        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
        self._list.InsertColumn(0, "Path", width=420)
        self._list.InsertColumn(1, "Chunks", width=70)
        self._list.InsertColumn(2, "Indexed At", width=150)
        self._list.InsertColumn(3, "Embedding", width=220)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._status_label = wx.StaticText(self, label="")
        outer.Add(self._status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._add_files_btn.Bind(wx.EVT_BUTTON, self._on_add_files)
        self._add_folder_btn.Bind(wx.EVT_BUTTON, self._on_add_folder)
        self._reindex_btn.Bind(wx.EVT_BUTTON, self._on_reindex_selected)
        self._reindex_all_btn.Bind(wx.EVT_BUTTON, self._on_reindex_all)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_view_document)
        bind_hover_path_tooltip(self._list, lambda row: self._documents[row].path if 0 <= row < len(self._documents) else None)

        self.reload()

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self.reload()

    def set_file_name_display(self, mode: str) -> None:
        self._file_name_display = mode
        self.reload()

    def reload(self) -> None:
        self._documents = self._repository.list(self._profile_id)

        self._list.DeleteAllItems()
        for row, document in enumerate(self._documents):
            self._list.InsertItem(row, format_display_path(document.path, self._file_name_display))
            self._list.SetItem(row, 1, str(document.chunk_count))
            self._list.SetItem(row, 2, document.indexed_at or "")
            self._list.SetItem(row, 3, f"{document.embedding_backend or ''} / {document.embedding_model or ''}")

        self._update_button_states(None)

    def _selected_document(self) -> Optional[Document]:
        index = self._list.GetFirstSelected()
        if index == -1:
            return None
        return self._documents[index]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        has_selection = self._selected_document() is not None
        busy = self._async.is_busy()
        self._reindex_btn.Enable(has_selection and not busy)
        self._remove_btn.Enable(has_selection and not busy)

    def _current_profile(self):
        return self._profile_repository.get(self._profile_id)

    # ------------------------------------------------------------------
    # Add / index
    # ------------------------------------------------------------------
    def _on_add_files(self, event: wx.CommandEvent) -> None:
        dlg = wx.FileDialog(
            self,
            message="Choose text files to index",
            wildcard=FILE_DIALOG_WILDCARD,
            style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            paths = [Path(p) for p in dlg.GetPaths()]
            self._start_indexing(paths, force=False)
        dlg.Destroy()

    def _on_add_folder(self, event: wx.CommandEvent) -> None:
        dlg = wx.DirDialog(self, message="Choose a folder to index (searched recursively)")
        if dlg.ShowModal() == wx.ID_OK:
            self._start_indexing([Path(dlg.GetPath())], force=False)
        dlg.Destroy()

    def _on_reindex_selected(self, event: wx.CommandEvent) -> None:
        document = self._selected_document()
        if document is None:
            return
        self._start_indexing([Path(document.path)], force=True)

    def _on_reindex_all(self, event: wx.CommandEvent) -> None:
        if not self._documents:
            wx.MessageBox("No documents to reindex yet.", "Reindex All", wx.OK | wx.ICON_INFORMATION, self)
            return
        paths = [Path(document.path) for document in self._documents]
        self._start_indexing(paths, force=True)

    def _start_indexing(self, paths: List[Path], force: bool) -> None:
        profile = self._current_profile()
        if profile is None:
            return

        def on_progress(done: int, total: int, path: str) -> None:
            wx.CallAfter(self._status_label.SetLabel, f"Indexing {done}/{total}: {Path(path).name}")

        def on_success(summary: IndexRunSummary) -> None:
            self.reload()
            message = f"Indexed {summary.indexed}, skipped {summary.skipped} (unchanged)"
            if summary.errors:
                message += f", {len(summary.errors)} error(s)"
            self._status_label.SetLabel(message)
            if summary.errors:
                details = "\n".join(summary.errors[:10])
                if len(summary.errors) > 10:
                    details += f"\n... and {len(summary.errors) - 10} more"
                wx.MessageBox(details, "Some files could not be indexed", wx.OK | wx.ICON_WARNING, self)
            self._on_status(f"Indexed {summary.indexed} document(s) in profile \"{profile.name}\"")

        def on_error(exc: Exception) -> None:
            self._status_label.SetLabel("Indexing failed.")
            wx.MessageBox(f"Indexing failed:\n\n{exc}", "Indexing error", wx.OK | wx.ICON_ERROR, self)

        buttons = [
            self._add_files_btn,
            self._add_folder_btn,
            self._reindex_btn,
            self._reindex_all_btn,
            self._remove_btn,
        ]
        self._status_label.SetLabel("Indexing...")
        self._async.run(
            work=lambda: self._repository.index_paths(profile, paths, on_progress=on_progress, force=force),
            on_success=on_success,
            on_error=on_error,
            disable=buttons,
        )

    # ------------------------------------------------------------------
    # View content - same Scintilla-based DocumentViewerFrame the Search
    # page opens on a result, just with no matches to highlight/list.
    # ------------------------------------------------------------------
    def _get_viewer_frame(self) -> DocumentViewerFrame:
        if self._viewer_frame is None:
            self._viewer_frame = DocumentViewerFrame(self)
            self._viewer_frame.Bind(wx.EVT_CLOSE, self._on_viewer_closed)
        return self._viewer_frame

    def _on_viewer_closed(self, event: wx.CloseEvent) -> None:
        self._viewer_frame = None
        event.Skip()

    def _on_view_document(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if index < 0 or index >= len(self._documents):
            return
        document = self._documents[index]

        viewer = self._get_viewer_frame()
        # Show/Raise before feeding it content - the viewer's splitter can
        # leave stale rendering behind if its split state changes before the
        # top-level window has ever been mapped (a GTK realization quirk).
        viewer.Show()
        viewer.Raise()
        viewer.show_loading(document.path)

        def on_success(text: str) -> None:
            viewer.show_document(document.path, text, [])

        def on_error(exc: Exception) -> None:
            viewer.show_error(document.path, str(exc))

        self._viewer_async.run(
            work=lambda: extract_text(Path(document.path)),
            on_success=on_success,
            on_error=on_error,
        )

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------
    def _on_remove(self, event: wx.CommandEvent) -> None:
        document = self._selected_document()
        if document is None:
            return
        confirm = wx.MessageBox(
            f'Remove "{document.path}" from the index?\n\n'
            "This only removes it from this app's index - the file itself is untouched.",
            "Confirm remove",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._repository.remove(document.id)
            self.reload()
