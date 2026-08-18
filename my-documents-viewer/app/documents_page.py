from pathlib import Path
from typing import Callable, List, Optional

import wx
import wx.dataview as dv

from .async_task import AsyncTaskRunner
from .data_import import DataFilePreview, ImportMapping
from .data_import import preview as preview_data_file
from .data_import_dialog import ImportMappingDialog
from .document_viewer import DocumentViewerFrame
from .file_display import FILE_NAME_DISPLAY_DEFAULT, format_document_label
from .models import KIND_CONTAINER, KIND_FILE, KIND_RECORD, Document
from .repositories import EMBED_BATCH_SIZE, DocumentRepository, IndexRunSummary, ProfileRepository

FILE_DIALOG_WILDCARD = "Text/Markdown files (*.txt;*.md)|*.txt;*.md|All files (*.*)|*.*"
DATA_FILE_DIALOG_WILDCARD = "Data files (*.csv;*.json)|*.csv;*.json|All files (*.*)|*.*"


class DocumentsPage(wx.Panel):
    """CRUD + indexing screen for a profile's documents: add individual
    files or whole folders (walked recursively for .txt/.md), import a
    structured data file (CSV/JSON) as one container document with one
    record child per row/object (see app/data_import.py), reindex (single
    or all, e.g. after an embedding model change), and remove.

    Shown as a tree (wx.dataview.TreeListCtrl, not a flat wx.ListCtrl) -
    plain files and containers are the top level; a container's records are
    fetched lazily the first time it's expanded (see _on_item_expanding),
    the same dummy-placeholder-child pattern my-redis-viewer's KeyTreeView
    uses for its own lazy tree."""

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
        self._on_status = on_status or (lambda text: None)
        self._file_name_display = file_name_display
        self._async = AsyncTaskRunner(self)
        # Separate from `_async`: opening the content viewer shouldn't be
        # blocked by (or block) an indexing/import run in flight on this page.
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
        self._import_data_btn = wx.Button(self, label="Import Data File...")
        self._reindex_btn = wx.Button(self, label="Reindex Selected")
        self._reindex_all_btn = wx.Button(self, label="Reindex All")
        self._embed_btn = wx.Button(self, label="Generate Embeddings")
        self._remove_btn = wx.Button(self, label="Remove")
        for btn in (
            self._add_files_btn,
            self._add_folder_btn,
            self._import_data_btn,
            self._reindex_btn,
            self._reindex_all_btn,
            self._embed_btn,
            self._remove_btn,
        ):
            toolbar.Add(btn, 0, wx.RIGHT, 8)
        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._tree = dv.TreeListCtrl(self, style=dv.TL_DEFAULT_STYLE | wx.BORDER_SUNKEN)
        self._tree.AppendColumn("Name", width=420)
        self._tree.AppendColumn("Chunks", width=90)
        self._tree.AppendColumn("Indexed At", width=150)
        self._tree.AppendColumn("Embedding", width=220)
        outer.Add(self._tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Only shown while an index/import/embed run is in flight - a
        # multi-minute paid-API embedding pass must not look identical to
        # "hung" with only a static status string (see _update_progress).
        self._progress_gauge = wx.Gauge(self, range=100)
        self._progress_gauge.Hide()
        outer.Add(self._progress_gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._status_label = wx.StaticText(self, label="")
        outer.Add(self._status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._add_files_btn.Bind(wx.EVT_BUTTON, self._on_add_files)
        self._add_folder_btn.Bind(wx.EVT_BUTTON, self._on_add_folder)
        self._import_data_btn.Bind(wx.EVT_BUTTON, self._on_import_data_file)
        self._reindex_btn.Bind(wx.EVT_BUTTON, self._on_reindex_selected)
        self._reindex_all_btn.Bind(wx.EVT_BUTTON, self._on_reindex_all)
        self._embed_btn.Bind(wx.EVT_BUTTON, self._on_generate_embeddings)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._tree.Bind(dv.EVT_TREELIST_ITEM_EXPANDING, self._on_item_expanding)
        self._tree.Bind(dv.EVT_TREELIST_SELECTION_CHANGED, self._update_button_states)
        self._tree.Bind(dv.EVT_TREELIST_ITEM_ACTIVATED, self._on_view_document)

        self.reload()

    def set_profile(self, profile_id: int) -> None:
        self._profile_id = profile_id
        self.reload()

    def set_file_name_display(self, mode: str) -> None:
        self._file_name_display = mode
        self.reload()

    def reload(self) -> None:
        self._tree.DeleteAllItems()
        root = self._tree.GetRootItem()
        for document in self._repository.list_top_level(self._profile_id):
            self._append_item(root, document, container=None)
        self._update_button_states(None)

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------
    def _append_item(self, parent_item, document: Document, container: Optional[Document]):
        item = self._tree.AppendItem(parent_item, "")
        self._set_item_document(item, document, container)
        if document.kind == KIND_CONTAINER:
            self._tree.AppendItem(item, "")  # placeholder - see _on_item_expanding
        return item

    def _set_item_document(self, item, document: Document, container: Optional[Document]) -> None:
        self._tree.SetItemData(item, document)
        self._tree.SetItemText(item, 0, format_document_label(document, container, self._file_name_display))
        self._tree.SetItemText(item, 1, self._chunks_column(document))
        self._tree.SetItemText(item, 2, document.indexed_at or "")
        self._tree.SetItemText(item, 3, self._embedding_column(document))

    @staticmethod
    def _chunks_column(document: Document) -> str:
        if document.kind == KIND_CONTAINER:
            return f"{(document.properties or {}).get('row_count', 0)} record(s)"
        return str(document.chunk_count)

    @staticmethod
    def _embedding_column(document: Document) -> str:
        if document.embedding_backend:
            return f"{document.embedding_backend} / {document.embedding_model}"
        if document.kind == KIND_RECORD:
            return "(full-text only)"
        return ""

    def _on_item_expanding(self, event: dv.TreeListEvent) -> None:
        item = event.GetItem()
        container = self._tree.GetItemData(item)
        if container is None or container.kind != KIND_CONTAINER:
            return
        first_child = self._tree.GetFirstChild(item)
        if not first_child.IsOk() or self._tree.GetItemData(first_child) is not None:
            return  # already populated, or genuinely has no children
        self._tree.DeleteItem(first_child)
        for record in self._repository.list_children(container.id):
            self._append_item(item, record, container)

    def _selected_document(self) -> Optional[Document]:
        item = self._tree.GetSelection()
        if not item.IsOk():
            return None
        return self._tree.GetItemData(item)

    def _selected_container(self) -> Optional[Document]:
        """The selected record's parent Document, for display purposes -
        walked from the tree (GetItemParent) rather than a repository
        round-trip, since it's already in memory."""
        item = self._tree.GetSelection()
        if not item.IsOk():
            return None
        parent_item = self._tree.GetItemParent(item)
        if not parent_item.IsOk():
            return None
        return self._tree.GetItemData(parent_item)

    def _update_button_states(self, event) -> None:
        document = self._selected_document()
        busy = self._async.is_busy()
        self._reindex_btn.Enable(document is not None and not busy and document.kind != KIND_RECORD)
        self._embed_btn.Enable(document is not None and not busy and document.kind == KIND_CONTAINER)
        self._remove_btn.Enable(document is not None and not busy)
        if event is not None:
            event.Skip()

    def _current_profile(self):
        return self._profile_repository.get(self._profile_id)

    def _all_buttons(self) -> List[wx.Button]:
        return [
            self._add_files_btn,
            self._add_folder_btn,
            self._import_data_btn,
            self._reindex_btn,
            self._reindex_all_btn,
            self._embed_btn,
            self._remove_btn,
        ]

    def _update_progress(self, done: int, total: int, text: str) -> None:
        self._status_label.SetLabel(text)
        if total > 0:
            self._progress_gauge.SetRange(total)
            self._progress_gauge.SetValue(min(done, total))

    def _run_async(self, **kwargs) -> None:
        """Defer to the *next* event-loop iteration before calling
        self._async.run(**kwargs).

        AsyncTaskRunner only clears its busy flag *after* the finished run's
        on_success/on_done callbacks have returned (see
        async_task.AsyncTaskRunner._consumer's `finally: self._busy =
        False`, which runs after on_done()). Several flows here start a new
        run from *inside* a previous run's on_success/on_done - e.g. the
        preview parse's on_success opening the mapping dialog and, once
        confirmed, immediately starting the actual import; or _run_steps
        advancing to its next queued step from on_done_extra. Calling
        self._async.run() directly at that point would see _busy still True
        and be silently dropped (AsyncTaskRunner's documented behavior for
        an overlapping call) - which looked exactly like "stuck on
        Importing..." forever, since nothing was left to ever clear that
        status label. wx.CallAfter here queues the run for the next
        iteration of the event loop, by which point the previous run's
        _consumer (including its `finally`) has fully returned."""
        print(f"[documents_page] scheduling async run (busy={self._async.is_busy()})")
        wx.CallAfter(self._async.run, **kwargs)

    # ------------------------------------------------------------------
    # Add / index plain files
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
        if document.kind == KIND_CONTAINER:
            self._start_reindex_container(document)
        else:
            self._start_indexing([Path(document.path)], force=True)

    def _on_reindex_all(self, event: wx.CommandEvent) -> None:
        top_level = self._repository.list_top_level(self._profile_id)
        if not top_level:
            wx.MessageBox("No documents to reindex yet.", "Reindex All", wx.OK | wx.ICON_INFORMATION, self)
            return
        files = [Path(d.path) for d in top_level if d.kind == KIND_FILE]
        containers = [d for d in top_level if d.kind == KIND_CONTAINER]

        # AsyncTaskRunner silently ignores an overlapping .run() call, so
        # the file batch and each container's (separately consent-gated)
        # reindex must run strictly one after another, not fired together.
        steps: List[Callable[[Callable[[], None]], None]] = []
        if files:
            steps.append(lambda cont: self._start_indexing(files, force=True, on_done_extra=cont))
        for container in containers:
            steps.append(lambda cont, c=container: self._start_reindex_container(c, on_done_extra=cont))
        self._run_steps(steps)

    @staticmethod
    def _run_steps(steps: List[Callable[[Callable[[], None]], None]]) -> None:
        remaining = list(steps)

        def advance() -> None:
            if remaining:
                remaining.pop(0)(advance)

        advance()

    def _start_indexing(
        self, paths: List[Path], force: bool, on_done_extra: Optional[Callable[[], None]] = None
    ) -> None:
        profile = self._current_profile()
        if profile is None:
            if on_done_extra:
                on_done_extra()
            return

        def on_progress(done: int, total: int, path: str) -> None:
            wx.CallAfter(self._update_progress, done, total, f"Indexing {done}/{total}: {Path(path).name}")

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

        def on_done() -> None:
            self._progress_gauge.Hide()
            self.Layout()
            if on_done_extra:
                on_done_extra()

        self._status_label.SetLabel("Indexing...")
        self._progress_gauge.SetValue(0)
        self._progress_gauge.Show()
        self.Layout()
        self._run_async(
            work=lambda: self._repository.index_paths(profile, paths, on_progress=on_progress, force=force),
            on_success=on_success,
            on_error=on_error,
            on_done=on_done,
            disable=self._all_buttons(),
        )

    # ------------------------------------------------------------------
    # Import a structured data file (CSV/JSON) as container + records
    # ------------------------------------------------------------------
    def _on_import_data_file(self, event: wx.CommandEvent) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        dlg = wx.FileDialog(
            self,
            message="Choose a CSV or JSON file to import",
            wildcard=DATA_FILE_DIALOG_WILDCARD,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = Path(dlg.GetPath())
        dlg.Destroy()
        print(f"[documents_page] _on_import_data_file: chosen path={path}")

        self._status_label.SetLabel(f"Reading {path.name}...")

        def on_success(file_preview: DataFilePreview) -> None:
            print(f"[documents_page] preview succeeded: {file_preview.row_count} row(s) - opening mapping dialog")
            self._status_label.SetLabel("")
            self._open_mapping_dialog(profile, path, file_preview)

        def on_error(exc: Exception) -> None:
            print(f"[documents_page] preview FAILED: {exc!r}")
            self._status_label.SetLabel("")
            wx.MessageBox(f"Could not read {path.name}:\n\n{exc}", "Import error", wx.OK | wx.ICON_ERROR, self)

        # Parsing (through AsyncTaskRunner, not the UI thread) - row count
        # needs a full parse, which shouldn't stall the UI just to populate
        # the mapping dialog for a large file.
        self._run_async(
            work=lambda: preview_data_file(path),
            on_success=on_success,
            on_error=on_error,
            disable=[self._import_data_btn],
        )

    def _open_mapping_dialog(self, profile, path: Path, file_preview: DataFilePreview) -> None:
        dlg = ImportMappingDialog(self, path, file_preview)
        result = dlg.ShowModal()
        print(f"[documents_page] mapping dialog closed with {'OK' if result == wx.ID_OK else 'Cancel'}")
        if result == wx.ID_OK:
            mapping = dlg.get_mapping()
            dlg.Destroy()
            print(f"[documents_page] mapping: content_columns={mapping.content_columns} "
                  f"id_column={mapping.id_column} title_column={mapping.title_column}")
            self._decide_embedding_and_import(profile, path, mapping, file_preview.row_count, force=False)
        else:
            dlg.Destroy()

    def _decide_embedding_and_import(
        self,
        profile,
        path: Path,
        mapping: ImportMapping,
        row_count: int,
        force: bool,
        on_done_extra: Optional[Callable[[], None]] = None,
    ) -> None:
        if not self._repository.vector_enabled:
            print("[documents_page] vector search unavailable - importing FTS-only")
            self._start_import(profile, path, mapping, embed=False, force=force, on_done_extra=on_done_extra)
            return

        if profile.embedding_backend == "fastembed":
            # Local/free - only a time cost, communicated by the progress
            # bar, so no consent prompt is needed even for a large import.
            print("[documents_page] fastembed backend - auto-embedding without a consent prompt")
            self._start_import(profile, path, mapping, embed=True, force=force, on_done_extra=on_done_extra)
            return

        print(f"[documents_page] {profile.embedding_backend} backend - asking for embedding consent")
        dlg = wx.MessageDialog(
            self,
            f"This will send {row_count} row(s) to {profile.embedding_backend} for embedding "
            f"(in batches of up to {EMBED_BATCH_SIZE} rows per request), using your API key.\n\n"
            "Generate embeddings now, import for full-text search only for now (you can\n"
            "generate embeddings later via \"Generate Embeddings\"), or cancel?",
            "Generate embeddings?",
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
        )
        dlg.SetYesNoCancelLabels("Generate embeddings now", "Full-text only for now", "Cancel")
        choice = dlg.ShowModal()
        dlg.Destroy()
        print(f"[documents_page] consent dialog choice: {choice} (YES={wx.ID_YES} NO={wx.ID_NO} CANCEL={wx.ID_CANCEL})")

        if choice == wx.ID_CANCEL:
            if on_done_extra:
                on_done_extra()
            return
        self._start_import(profile, path, mapping, embed=(choice == wx.ID_YES), force=force, on_done_extra=on_done_extra)

    def _start_reindex_container(self, container: Document, on_done_extra: Optional[Callable[[], None]] = None) -> None:
        profile = self._current_profile()
        if profile is None:
            if on_done_extra:
                on_done_extra()
            return
        properties = container.properties or {}
        mapping = ImportMapping(
            content_columns=properties.get("content_columns", []),
            id_column=properties.get("id_column"),
            title_column=properties.get("title_column"),
        )
        # Reindexing a container routes through the exact same
        # embedding-consent step as a first-time import - never a silent
        # force=True re-embed of potentially thousands of records.
        self._decide_embedding_and_import(
            profile, Path(container.path), mapping, properties.get("row_count", 0), force=True, on_done_extra=on_done_extra
        )

    def _start_import(
        self,
        profile,
        path: Path,
        mapping: ImportMapping,
        embed: bool,
        force: bool = False,
        on_done_extra: Optional[Callable[[], None]] = None,
    ) -> None:
        print(f"[documents_page] _start_import: path={path} embed={embed} force={force}")

        def on_progress(done: int, total: int, label: str) -> None:
            print(f"[documents_page] on_progress: {done}/{total} ({label})")
            text = f"Embedding {done}/{total}..." if label == "embedding" else f"Importing {done}/{total} row(s)..."
            wx.CallAfter(self._update_progress, done, total, text)

        def on_success(summary: IndexRunSummary) -> None:
            print(f"[documents_page] import on_success: {summary}")
            self.reload()
            if summary.skipped and not summary.records_created and not summary.records_updated:
                message = f'"{path.name}" is unchanged since its last import - skipped.'
            else:
                message = (
                    f'Imported "{path.name}": {summary.records_created} new, '
                    f"{summary.records_updated} updated, {summary.records_removed} removed, "
                    f"{summary.records_skipped} unchanged"
                )
                if summary.embedded_count:
                    message += f"; embedded {summary.embedded_count} record(s)"
            self._status_label.SetLabel(message)
            self._on_status(f'Imported "{path.name}" into profile "{profile.name}"')

        def on_error(exc: Exception) -> None:
            print(f"[documents_page] import on_error: {exc!r}")
            self._status_label.SetLabel("Import failed.")
            wx.MessageBox(f"Import failed:\n\n{exc}", "Import error", wx.OK | wx.ICON_ERROR, self)

        def on_done() -> None:
            print("[documents_page] import on_done")
            self._progress_gauge.Hide()
            self.Layout()
            if on_done_extra:
                on_done_extra()

        self._status_label.SetLabel("Importing...")
        self._progress_gauge.SetValue(0)
        self._progress_gauge.Show()
        self.Layout()
        self._run_async(
            work=lambda: self._repository.import_data_file(
                profile, path, mapping, embed=embed, on_progress=on_progress, force=force
            ),
            on_success=on_success,
            on_error=on_error,
            on_done=on_done,
            disable=self._all_buttons(),
        )

    def _on_generate_embeddings(self, event: wx.CommandEvent) -> None:
        document = self._selected_document()
        if document is None or document.kind != KIND_CONTAINER:
            return
        profile = self._current_profile()
        if profile is None:
            return
        if not self._repository.vector_enabled:
            wx.MessageBox(
                "Vector search is unavailable on this build.", "Generate Embeddings", wx.OK | wx.ICON_INFORMATION, self
            )
            return

        def on_progress(done: int, total: int, label: str) -> None:
            wx.CallAfter(self._update_progress, done, total, f"Embedding {done}/{total}...")

        def on_success(count: int) -> None:
            self.reload()
            self._status_label.SetLabel(f"Embedded {count} record(s)." if count else "Nothing to embed - already up to date.")

        def on_error(exc: Exception) -> None:
            self._status_label.SetLabel("Embedding failed.")
            wx.MessageBox(f"Embedding failed:\n\n{exc}", "Generate Embeddings", wx.OK | wx.ICON_ERROR, self)

        def on_done() -> None:
            self._progress_gauge.Hide()
            self.Layout()

        self._status_label.SetLabel("Embedding...")
        self._progress_gauge.SetValue(0)
        self._progress_gauge.Show()
        self.Layout()
        self._run_async(
            work=lambda: self._repository.embed_records(profile, document.id, on_progress=on_progress),
            on_success=on_success,
            on_error=on_error,
            on_done=on_done,
            disable=self._all_buttons(),
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

    def _on_view_document(self, event) -> None:
        document = self._selected_document()
        if document is None:
            return
        container = self._selected_container() if document.kind == KIND_RECORD else None
        label = format_document_label(document, container, self._file_name_display)

        viewer = self._get_viewer_frame()
        # Show/Raise before feeding it content - the viewer's splitter can
        # leave stale rendering behind if its split state changes before the
        # top-level window has ever been mapped (a GTK realization quirk).
        viewer.Show()
        viewer.Raise()
        viewer.show_loading(label)

        def on_error(exc: Exception) -> None:
            viewer.show_error(label, str(exc))

        if document.kind == KIND_CONTAINER:
            # A container has no content of its own - list its records as a
            # data grid instead (see DocumentViewerPanel.show_records).
            def on_records(records: List[Document]) -> None:
                viewer.show_records(label, document.properties, records)

            self._viewer_async.run(
                work=lambda: self._repository.list_children(document.id),
                on_success=on_records,
                on_error=on_error,
            )
            return

        properties = document.properties if document.kind == KIND_RECORD else None

        def on_success(text: str) -> None:
            viewer.show_document(label, text, [], properties=properties)

        self._viewer_async.run(
            work=lambda: self._repository.get_content(document.id),
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
        container = self._selected_container() if document.kind == KIND_RECORD else None
        label = format_document_label(document, container, self._file_name_display)
        extra = ""
        if document.kind == KIND_CONTAINER:
            row_count = (document.properties or {}).get("row_count", 0)
            extra = f"\n\nThis also removes its {row_count} record(s)."
        confirm = wx.MessageBox(
            f'Remove "{label}" from the index?{extra}\n\n'
            "This only removes it from this app's index - the source file itself is untouched.",
            "Confirm remove",
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if confirm == wx.YES:
            self._repository.remove(document.id)
            self.reload()
