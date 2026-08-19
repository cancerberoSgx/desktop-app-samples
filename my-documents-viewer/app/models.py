from dataclasses import dataclass, field
from typing import List, Optional

from .chunking import CHUNK_SIZE


@dataclass
class Profile:
    """A document "kind" (e.g. "History", "Development", "Contracts") -
    scopes which documents are indexed together and which embedding
    backend/model/dimension is used to embed them."""

    id: Optional[int]
    name: str
    embedding_backend: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    chunk_size: int = CHUNK_SIZE
    # Text-generation config for the Chat page (app/chat_page.py,
    # app/chat_service.py) - independent of the embedding config above, but
    # shares its openai_api_key/gemini_api_key (same providers). None means
    # chat hasn't been configured for this profile yet - see
    # app/chat/__init__.py::get_chat_backend.
    chat_backend: Optional[str] = None
    chat_model: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# Document.kind values. 'file' (the default) is today's one-document-per-
# source-file case, unchanged. A structured data import (see
# app/data_import.py, DocumentRepository.import_data_file) instead produces
# one 'container' document (the source .csv/.json, no content/chunks of its
# own) with many 'record' children under it via parent_document_id - one per
# row/object.
KIND_FILE = "file"
KIND_CONTAINER = "container"
KIND_RECORD = "record"


@dataclass
class Document:
    """One indexed source file (.txt/.md for now - see app/text_extract.py
    for where future formats would plug in), OR one node of a structured
    data import - see the KIND_* constants above and app/data_import.py.

    `parent_document_id`/`kind`/`row_key`/`properties` are all None/default
    for a plain file - they only carry meaning for a container/record pair.
    `row_key` is the stable per-row identity used to match a record back to
    its source row across re-imports; `properties` is the parsed
    properties_json blob (container: import config: format/mapping/row
    count; record: the row's raw original field values) - display-only,
    never used by search."""

    id: Optional[int]
    profile_id: int
    path: str
    content_hash: str
    size_bytes: int = 0
    mtime: Optional[str] = None
    chunk_count: int = 0
    indexed_at: Optional[str] = None
    embedding_backend: Optional[str] = None
    embedding_model: Optional[str] = None
    parent_document_id: Optional[int] = None
    kind: str = KIND_FILE
    row_key: Optional[str] = None
    properties: Optional[dict] = None


@dataclass
class SearchResult:
    """One hybrid-search hit: a chunk, its parent document, and the ranks/
    score that produced its position - see repositories.DocumentRepository.
    hybrid_search for how these are combined via reciprocal rank fusion.

    `chunk_index`/`start_offset`/`end_offset` are the same values chunking.
    chunk_text computed at index time (see chunking.Chunk) - they locate this
    chunk's span within the *document's extracted text*, and are what
    DocumentViewerPanel uses to scroll to and highlight this hit."""

    chunk_id: int
    document_id: int
    document_path: str
    snippet: str
    score: float
    chunk_index: int = 0
    start_offset: int = 0
    end_offset: int = 0
    fts_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_distance: Optional[float] = None

    @property
    def is_vector_only(self) -> bool:
        """True if this hit came only from vector similarity, with no
        full-text term match - there's no lexical span to point to within
        the chunk, so the viewer highlights the whole chunk in a distinct
        color rather than implying specific matched words."""
        return self.fts_rank is None and self.vector_rank is not None


@dataclass
class DocumentSearchResult:
    """One document's search hits, grouped from a flat SearchResult list
    (see repositories.group_by_document) so the Search page can show one row
    per document instead of one per matching chunk. `matches` is sorted by
    score descending - the same order the table of contents and prev/next
    navigation in DocumentViewerPanel use - so the best-matching chunk is
    always first."""

    document_id: int
    document_path: str
    score: float
    matches: List[SearchResult] = field(default_factory=list)

    @property
    def best_match(self) -> SearchResult:
        return self.matches[0]


@dataclass
class Conversation:
    """One named, ordered chat thread - scoped to a profile the same way
    documents are (see app/conversation_repository.py). `title` starts as a
    placeholder ("New Conversation") and is either auto-derived from the
    first question asked in it or renamed by the user - see
    ChatPage._maybe_auto_title."""

    id: Optional[int]
    profile_id: int
    title: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ChatMessage:
    """One turn in a Conversation. `references` (assistant turns only) is the
    list of chunks hybrid_search() retrieved to answer this turn - the same
    SearchResult shape Search page results use, so ChatPage can open
    DocumentViewerFrame on one exactly the way SearchPage does (see
    app/document_open.py). Empty for user messages and for an assistant turn
    that retrieved nothing."""

    id: Optional[int]
    conversation_id: int
    role: str  # 'user' | 'assistant'
    content: str
    created_at: Optional[str] = None
    references: List[SearchResult] = field(default_factory=list)
