from dataclasses import dataclass
from typing import Optional

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
    hybrid_search for how these are combined via reciprocal rank fusion."""

    chunk_id: int
    document_id: int
    document_path: str
    snippet: str
    score: float
    fts_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_distance: Optional[float] = None
