import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .chunking import chunk_text
from .embeddings import EmbeddingError, get_backend
from .models import Document, DocumentSearchResult, Profile, SearchResult
from .text_extract import extract_text, is_supported
from .vector_codec import serialize_vector

CURRENT_PROFILE_SETTING_KEY = "current_profile_id"
SIDEBAR_COLLAPSED_SETTING_KEY = "sidebar_collapsed"

# Reciprocal Rank Fusion constant - the standard choice (see e.g. Cormack et
# al.'s RRF paper); combines the full-text and vector rankings without
# needing their raw scores (bm25 and cosine/L2 distance aren't on
# comparable scales) to be normalized against each other.
RRF_K = 60
DEFAULT_SEARCH_LIMIT = 20
FTS_CANDIDATE_LIMIT = 50
VECTOR_CANDIDATE_LIMIT = 50
SNIPPET_MAX_LENGTH = 240

SEARCH_MODES = ("hybrid", "fulltext", "vector")


@dataclass
class IndexRunSummary:
    """Result of one DocumentRepository.index_paths() call, for the
    Documents page to report back to the user."""

    indexed: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)


class ProfileRepository:
    """CRUD for `profiles` (pure SQL against SQLite). Deleting a profile
    cascades to its documents/chunks (see the profile_id FKs); its sqlite-vec
    table is dropped separately by the caller (see MainFrame._on_delete /
    DocumentRepository.reset_vector_index) since SQLite foreign keys don't
    reach into virtual tables."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, name: str) -> Profile:
        cursor = self._conn.execute("INSERT INTO profiles (name) VALUES (?)", (name,))
        self._conn.commit()
        return self.get(cursor.lastrowid)

    def list(self) -> List[Profile]:
        rows = self._conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()
        return [self._row_to_profile(row) for row in rows]

    def get(self, profile_id: int) -> Optional[Profile]:
        row = self._conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return self._row_to_profile(row) if row else None

    def update(self, profile: Profile) -> Profile:
        self._conn.execute(
            """
            UPDATE profiles
            SET name = ?, embedding_backend = ?, embedding_model = ?, embedding_dim = ?,
                openai_api_key = ?, gemini_api_key = ?, chunk_size = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                profile.name,
                profile.embedding_backend,
                profile.embedding_model,
                profile.embedding_dim,
                profile.openai_api_key,
                profile.gemini_api_key,
                profile.chunk_size,
                profile.id,
            ),
        )
        self._conn.commit()
        return self.get(profile.id)

    def delete(self, profile_id: int) -> None:
        self._conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"],
            name=row["name"],
            embedding_backend=row["embedding_backend"],
            embedding_model=row["embedding_model"],
            embedding_dim=row["embedding_dim"],
            openai_api_key=row["openai_api_key"],
            gemini_api_key=row["gemini_api_key"],
            chunk_size=row["chunk_size"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite): which profile was
    last active, and whether the sidebar was collapsed."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: Optional[str]) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_current_profile_id(self) -> Optional[int]:
        value = self.get(CURRENT_PROFILE_SETTING_KEY)
        return int(value) if value is not None else None

    def set_current_profile_id(self, profile_id: Optional[int]) -> None:
        self.set(CURRENT_PROFILE_SETTING_KEY, str(profile_id) if profile_id is not None else None)

    def get_sidebar_collapsed(self) -> bool:
        return self.get(SIDEBAR_COLLAPSED_SETTING_KEY) == "1"

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.set(SIDEBAR_COLLAPSED_SETTING_KEY, "1" if collapsed else "0")


class DocumentRepository:
    """CRUD + indexing/search for `documents`/`chunks`, scoped to a profile.

    Full-text search runs against the shared `chunks_fts` FTS5 table.
    Vector search runs against a *per-profile* sqlite-vec `vec0` table
    (`vec_chunks_<profile_id>`) - it can't be a single shared table because
    its column width is the profile's `embedding_dim`, which differs per
    embedding model. `vector_enabled` is resolved once at startup (see
    app/db/connection.vector_search_available) and threaded in here; when
    False every vector-related code path is skipped and this repository
    behaves as full-text-only.
    """

    def __init__(self, conn: sqlite3.Connection, vector_enabled: bool):
        self._conn = conn
        self._vector_enabled = vector_enabled

    @property
    def vector_enabled(self) -> bool:
        return self._vector_enabled

    # ------------------------------------------------------------------
    # Per-profile vec0 table management
    # ------------------------------------------------------------------
    @staticmethod
    def _vec_table(profile_id: int) -> str:
        return f"vec_chunks_{profile_id}"

    def _ensure_vec_table(self, profile: Profile) -> Optional[str]:
        if not self._vector_enabled:
            return None
        table = self._vec_table(profile.id)
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0(embedding float[{profile.embedding_dim}])"
        )
        self._conn.commit()
        return table

    def reset_vector_index(self, profile_id: int) -> None:
        """Drop this profile's vec0 table - call this after its embedding
        backend/model/dimension changes. Existing documents/chunks and their
        FTS entries are untouched (still full-text searchable); vector
        search for this profile comes back empty until documents are
        reindexed with the new model (see index_paths(..., force=True))."""
        if not self._vector_enabled:
            return
        self._conn.execute(f"DROP TABLE IF EXISTS {self._vec_table(profile_id)}")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Documents CRUD
    # ------------------------------------------------------------------
    def list(self, profile_id: int) -> List[Document]:
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE profile_id = ? ORDER BY path", (profile_id,)
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def get(self, document_id: int) -> Optional[Document]:
        row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._row_to_document(row) if row else None

    def remove(self, document_id: int) -> None:
        document = self.get(document_id)
        if document is None:
            return
        chunk_ids = [
            row["id"]
            for row in self._conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))
        ]
        # Cascades to `chunks` (ON DELETE CASCADE), which in turn removes
        # the matching chunks_fts rows via the chunks_ad trigger.
        self._conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self._conn.commit()

        if chunk_ids and self._vector_enabled:
            self._delete_vec_rows(document.profile_id, chunk_ids)

    def _delete_vec_rows(self, profile_id: int, chunk_ids: List[int]) -> None:
        table = self._vec_table(profile_id)
        placeholders = ",".join("?" for _ in chunk_ids)
        try:
            self._conn.execute(f"DELETE FROM {table} WHERE rowid IN ({placeholders})", chunk_ids)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # this profile's vec table was never created - nothing to clean up

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            profile_id=row["profile_id"],
            path=row["path"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
            mtime=row["mtime"],
            chunk_count=row["chunk_count"],
            indexed_at=row["indexed_at"],
            embedding_backend=row["embedding_backend"],
            embedding_model=row["embedding_model"],
        )

    # ------------------------------------------------------------------
    # Indexing - blocking (file I/O + embedding calls); callers from the UI
    # must run this through AsyncTaskRunner, never directly from a
    # wx.EVT_* handler.
    # ------------------------------------------------------------------
    def index_paths(
        self,
        profile: Profile,
        paths: List[Path],
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        force: bool = False,
    ) -> IndexRunSummary:
        """Index every supported file (see text_extract.SUPPORTED_EXTENSIONS)
        under `paths` into `profile` - files are added as-is, directories are
        walked recursively. A file whose content hash and embedding
        backend/model haven't changed since its last index is skipped unless
        `force=True` (used by "Reindex All" after an embedding model
        change). `on_progress(done, total, path)`, if given, is called
        before each file - it's invoked on the calling (worker) thread, so a
        caller updating wx widgets from it must hop back via wx.CallAfter,
        the same way AsyncTaskRunner's own callbacks do.
        """
        backend = get_backend(profile)
        vec_table = self._ensure_vec_table(profile)

        files = self._collect_files(paths)
        summary = IndexRunSummary()

        for position, file_path in enumerate(files, start=1):
            if on_progress:
                on_progress(position, len(files), str(file_path))
            try:
                self._index_one_file(profile, backend, vec_table, file_path, force, summary)
            except (OSError, UnicodeDecodeError) as exc:
                summary.errors.append(f"{file_path}: {exc}")
            except EmbeddingError as exc:
                summary.errors.append(f"{file_path}: {exc}")

        return summary

    @staticmethod
    def _collect_files(paths: List[Path]) -> List[Path]:
        files: List[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(sorted(p for p in path.rglob("*") if p.is_file() and is_supported(p)))
            elif path.is_file() and is_supported(path):
                files.append(path)

        seen = set()
        unique_files = []
        for candidate in files:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique_files.append(candidate)
        return unique_files

    def _index_one_file(
        self,
        profile: Profile,
        backend,
        vec_table: Optional[str],
        file_path: Path,
        force: bool,
        summary: IndexRunSummary,
    ) -> None:
        text = extract_text(file_path)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path_str = str(file_path.resolve())

        existing = self._conn.execute(
            "SELECT * FROM documents WHERE profile_id = ? AND path = ?", (profile.id, path_str)
        ).fetchone()

        unchanged = (
            existing is not None
            and existing["content_hash"] == content_hash
            and existing["embedding_backend"] == profile.embedding_backend
            and existing["embedding_model"] == profile.embedding_model
        )
        if unchanged and not force:
            summary.skipped += 1
            return

        chunks = chunk_text(text, chunk_size=profile.chunk_size)
        stat = file_path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")

        if existing is not None:
            document_id = existing["id"]
            old_chunk_ids = [
                row["id"]
                for row in self._conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))
            ]
            self._conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            if old_chunk_ids and vec_table:
                self._delete_vec_rows(profile.id, old_chunk_ids)
            self._conn.execute(
                """
                UPDATE documents
                SET content_hash = ?, size_bytes = ?, mtime = ?, chunk_count = ?,
                    indexed_at = datetime('now'), embedding_backend = ?, embedding_model = ?
                WHERE id = ?
                """,
                (content_hash, stat.st_size, mtime, len(chunks), profile.embedding_backend, profile.embedding_model, document_id),
            )
        else:
            cursor = self._conn.execute(
                """
                INSERT INTO documents
                    (profile_id, path, content_hash, size_bytes, mtime, chunk_count,
                     indexed_at, embedding_backend, embedding_model)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
                """,
                (profile.id, path_str, content_hash, stat.st_size, mtime, len(chunks), profile.embedding_backend, profile.embedding_model),
            )
            document_id = cursor.lastrowid
        self._conn.commit()

        if not chunks:
            summary.indexed += 1
            return

        chunk_ids = []
        for chunk in chunks:
            cursor = self._conn.execute(
                """
                INSERT INTO chunks (document_id, profile_id, chunk_index, text, start_offset, end_offset)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, profile.id, chunk.index, chunk.text, chunk.start_offset, chunk.end_offset),
            )
            chunk_ids.append(cursor.lastrowid)
        self._conn.commit()

        if vec_table:
            vectors = backend.embed([chunk.text for chunk in chunks])
            for chunk_id, vector in zip(chunk_ids, vectors):
                self._conn.execute(
                    f"INSERT INTO {vec_table} (rowid, embedding) VALUES (?, ?)",
                    (chunk_id, serialize_vector(vector)),
                )
            self._conn.commit()

        summary.indexed += 1

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------
    def hybrid_search(
        self,
        profile: Profile,
        query: str,
        mode: str = "hybrid",
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> List[SearchResult]:
        """`mode` is one of SEARCH_MODES:
        - "hybrid": FTS5 + vector, combined via Reciprocal Rank Fusion.
        - "fulltext": FTS5 (bm25) only.
        - "vector": vector similarity only (raises EmbeddingError if the
          profile's embedding backend can't be reached, e.g. a missing
          API key - "hybrid" mode instead swallows that and silently
          falls back to full-text-only, since it has a result set either
          way).
        """
        query = query.strip()
        if not query:
            return []

        fts_ranks: Dict[int, int] = {}
        if mode in ("hybrid", "fulltext"):
            fts_ranks = self._fts_search(profile.id, query, FTS_CANDIDATE_LIMIT)

        vector_ranks: Dict[int, int] = {}
        vector_distances: Dict[int, float] = {}
        if mode in ("hybrid", "vector") and self._vector_enabled:
            try:
                vector_ranks, vector_distances = self._vector_search(profile, query, VECTOR_CANDIDATE_LIMIT)
            except EmbeddingError:
                if mode == "vector":
                    raise

        chunk_ids = set(fts_ranks) | set(vector_ranks)
        if not chunk_ids:
            return []

        scored: List[Tuple[int, float]] = []
        for chunk_id in chunk_ids:
            score = 0.0
            if chunk_id in fts_ranks:
                score += 1.0 / (RRF_K + fts_ranks[chunk_id])
            if chunk_id in vector_ranks:
                score += 1.0 / (RRF_K + vector_ranks[chunk_id])
            scored.append((chunk_id, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)

        results = []
        for chunk_id, score in scored[:limit]:
            row = self._conn.execute(
                """
                SELECT chunks.text AS text, chunks.document_id AS document_id, documents.path AS path,
                       chunks.chunk_index AS chunk_index, chunks.start_offset AS start_offset,
                       chunks.end_offset AS end_offset
                FROM chunks JOIN documents ON documents.id = chunks.document_id
                WHERE chunks.id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if row is None:
                continue
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    document_id=row["document_id"],
                    document_path=row["path"],
                    snippet=_snippet(row["text"]),
                    score=score,
                    chunk_index=row["chunk_index"],
                    start_offset=row["start_offset"],
                    end_offset=row["end_offset"],
                    fts_rank=fts_ranks.get(chunk_id),
                    vector_rank=vector_ranks.get(chunk_id),
                    vector_distance=vector_distances.get(chunk_id),
                )
            )
        return results

    def _fts_search(self, profile_id: int, query: str, limit: int) -> Dict[int, int]:
        try:
            rows = self._conn.execute(
                """
                SELECT chunks.id AS chunk_id
                FROM chunks_fts JOIN chunks ON chunks.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ? AND chunks.profile_id = ?
                ORDER BY bm25(chunks_fts) LIMIT ?
                """,
                (_fts_match_expression(query), profile_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {row["chunk_id"]: rank for rank, row in enumerate(rows, start=1)}

    def _vector_search(self, profile: Profile, query: str, limit: int) -> Tuple[Dict[int, int], Dict[int, float]]:
        vec_table = self._vec_table(profile.id)
        backend = get_backend(profile)
        query_vector = backend.embed([query])[0]
        try:
            rows = self._conn.execute(
                f"SELECT rowid AS chunk_id, distance FROM {vec_table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (serialize_vector(query_vector), limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}, {}
        ranks = {row["chunk_id"]: rank for rank, row in enumerate(rows, start=1)}
        distances = {row["chunk_id"]: row["distance"] for row in rows}
        return ranks, distances


def _fts_match_expression(query: str) -> str:
    """Turn free-form user input into an FTS5 MATCH expression that won't
    raise a syntax error on stray quotes/operators: each word is quoted as
    its own phrase and OR'd together, so "invoice contract 2023" matches any
    chunk containing at least one of those words, ranked by bm25."""
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return '""'
    escaped = (token.replace('"', '""') for token in tokens)
    return " OR ".join(f'"{token}"' for token in escaped)


def group_by_document(results: List[SearchResult]) -> List[DocumentSearchResult]:
    """Fold a flat, one-row-per-chunk hybrid_search() result into one row
    per document, for SearchPage's results list. Each document's `matches`
    are ordered by position in the document (start_offset) - the reading
    order a table-of-contents wants - while `best_index` points at the
    highest-scoring chunk, so a viewer knows which one to open on. Documents
    are returned ordered by their best chunk's score, matching the overall
    relevance order hybrid_search() already produced."""
    by_document: Dict[int, List[SearchResult]] = {}
    for result in results:
        by_document.setdefault(result.document_id, []).append(result)

    grouped = []
    for document_id, matches in by_document.items():
        matches_by_position = sorted(matches, key=lambda m: m.start_offset)
        best_score = max(m.score for m in matches)
        best_index = max(range(len(matches_by_position)), key=lambda i: matches_by_position[i].score)
        grouped.append(
            DocumentSearchResult(
                document_id=document_id,
                document_path=matches[0].document_path,
                score=best_score,
                matches=matches_by_position,
                best_index=best_index,
            )
        )
    grouped.sort(key=lambda doc: doc.score, reverse=True)
    return grouped


def _snippet(text: str, max_length: int = SNIPPET_MAX_LENGTH) -> str:
    flattened = " ".join(text.split())
    if len(flattened) <= max_length:
        return flattened
    return flattened[:max_length].rstrip() + "…"
