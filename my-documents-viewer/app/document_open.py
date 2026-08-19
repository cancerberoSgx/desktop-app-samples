from typing import List

from .async_task import AsyncTaskRunner
from .document_viewer import DocumentViewerFrame
from .file_display import FILE_NAME_DISPLAY_DEFAULT, format_document_label
from .models import SearchResult
from .repositories import DocumentRepository


def open_document_at_matches(
    viewer: DocumentViewerFrame,
    repository: DocumentRepository,
    async_runner: AsyncTaskRunner,
    document_id: int,
    matches: List[SearchResult],
    file_name_display: str = FILE_NAME_DISPLAY_DEFAULT,
) -> None:
    """Open `viewer` on `document_id`'s content with `matches` highlighted -
    the "resolve document (+ its container, for a record) -> show_loading ->
    async get_content -> show_document" sequence SearchPage._load_and_show
    and ChatPage's reference-chip handler both need. A single-element
    `matches` list (one cited chunk) highlights exactly that chunk; a full
    DocumentSearchResult.matches list (SearchPage) highlights every match and
    opens on the best-scoring one - show_document() itself decides what
    "best" means (matches must already be sorted by score descending, as
    both callers' data already is).

    Silently does nothing if the document was removed since `document_id`
    was captured (e.g. a search result or an old chat reference pointing at
    a since-deleted document) - callers don't need their own existence
    check."""
    document = repository.get(document_id)
    if document is None:
        return
    container = repository.get(document.parent_document_id) if document.parent_document_id else None
    label = format_document_label(document, container, file_name_display)

    # Show/Raise before feeding it content - the viewer's splitter can leave
    # stale rendering behind if its split state changes before the top-level
    # window has ever been mapped (a GTK realization quirk).
    viewer.Show()
    viewer.Raise()
    viewer.show_loading(label)

    def on_success(text: str) -> None:
        viewer.show_document(label, document, text, matches, container=container)

    def on_error(exc: Exception) -> None:
        viewer.show_error(label, str(exc))

    # get_content() dispatches on the document's kind - a record's path isn't
    # a real file (see migration 0006), so this must go through the
    # repository rather than reading document.path directly.
    async_runner.run(
        work=lambda: repository.get_content(document_id),
        on_success=on_success,
        on_error=on_error,
    )
