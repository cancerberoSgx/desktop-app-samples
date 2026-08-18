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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Document:
    """One indexed source file (.txt/.md for now - see app/text_extract.py
    for where future formats would plug in)."""

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
    position in the document (start_offset) - the natural reading order for
    a table of contents - while `best_index` points at whichever entry has
    the highest score, for the viewer to jump to first."""

    document_id: int
    document_path: str
    score: float
    matches: List[SearchResult] = field(default_factory=list)
    best_index: int = 0

    @property
    def best_match(self) -> SearchResult:
        return self.matches[self.best_index]
