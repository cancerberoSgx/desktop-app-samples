import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .chunking import chunk_text
from .data_import import ImportMapping, build_record_text, read_records, resolve_row_key
from .embeddings import EmbeddingError, get_backend
from .file_display import FILE_NAME_DISPLAY_DEFAULT, FILE_NAME_DISPLAY_KEYS
from .models import KIND_CONTAINER, KIND_FILE, KIND_RECORD, Document, DocumentSearchResult, Profile, SearchResult
from .text_extract import extract_text, is_supported
from .vector_codec import serialize_vector

CURRENT_PROFILE_SETTING_KEY = "current_profile_id"
SIDEBAR_COLLAPSED_SETTING_KEY = "sidebar_collapsed"
FILE_NAME_DISPLAY_SETTING_KEY = "file_name_display"

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

# Batch size for embedding calls made during a structured data import (see
# DocumentRepository._batch_embed) - this is the *actual* HTTP request size
# for OpenAIEmbeddingBackend (which does no internal splitting of its own),
# harmlessly smaller than GeminiEmbeddingBackend's own 100-per-request
# internal batching, and irrelevant to FastEmbedBackend's local calls. Keeps
# a single request's payload/timeout/retry blast radius bounded even for a
# multi-thousand-row import.
EMBED_BATCH_SIZE = 64

# _delete_vec_rows chunks its DELETE statements at this size - some SQLite
# builds cap bound parameters at 999 (SQLITE_LIMIT_VARIABLE_NUMBER's older
# default), which a several-thousand-record container's chunk_ids would
# otherwise blow past in one statement.
VEC_DELETE_BATCH_SIZE = 500


@dataclass
class IndexRunSummary:
    """Result of one DocumentRepository.index_paths() or .import_data_file()
    call, for the Documents page to report back to the user. The
    records_*/embedded fields only apply to import_data_file - they stay at
    their defaults (0) for a plain index_paths() run."""

    indexed: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    records_created: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_removed: int = 0
    embedded_count: int = 0


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

    def get_file_name_display(self) -> str:
        value = self.get(FILE_NAME_DISPLAY_SETTING_KEY)
        return value if value in FILE_NAME_DISPLAY_KEYS else FILE_NAME_DISPLAY_DEFAULT

    def set_file_name_display(self, mode: str) -> None:
        self.set(FILE_NAME_DISPLAY_SETTING_KEY, mode)


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
    def list_top_level(self, profile_id: int) -> List[Document]:
        """Plain files and containers (not their record children) - what
        DocumentsPage's tree root is populated from. See list_children for
        a container's records, fetched lazily on expand."""
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE profile_id = ? AND parent_document_id IS NULL ORDER BY path",
            (profile_id,),
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def list_children(self, container_id: int) -> List[Document]:
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE parent_document_id = ? ORDER BY row_key", (container_id,)
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def get(self, document_id: int) -> Optional[Document]:
        row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._row_to_document(row) if row else None

    def remove(self, document_id: int) -> None:
        """Removes a document - for a container, its whole subtree of
        record children too. `chunks`/`chunks_fts` cascade away
        structurally (container -> records via parent_document_id's own ON
        DELETE CASCADE -> each record's chunks via chunks.document_id's
        existing FK -> chunks_fts via the chunks_ad trigger - verified this
        chain fires correctly even at several-thousand-record scale). Only
        vec0 rows need manual cleanup here, since virtual tables aren't
        covered by SQL FK cascades - gathered in one query across the whole
        subtree, not a per-child loop."""
        document = self.get(document_id)
        if document is None:
            return
        chunk_ids = [
            row["id"]
            for row in self._conn.execute(
                """
                SELECT id FROM chunks
                WHERE document_id IN (SELECT id FROM documents WHERE id = ? OR parent_document_id = ?)
                """,
                (document_id, document_id),
            )
        ]
        self._conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self._conn.commit()

        if chunk_ids and self._vector_enabled:
            self._delete_vec_rows(document.profile_id, chunk_ids)

    def _delete_vec_rows(self, profile_id: int, chunk_ids: List[int]) -> None:
        table = self._vec_table(profile_id)
        try:
            for start in range(0, len(chunk_ids), VEC_DELETE_BATCH_SIZE):
                batch = chunk_ids[start : start + VEC_DELETE_BATCH_SIZE]
                placeholders = ",".join("?" for _ in batch)
                self._conn.execute(f"DELETE FROM {table} WHERE rowid IN ({placeholders})", batch)
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
            parent_document_id=row["parent_document_id"],
            kind=row["kind"],
            row_key=row["row_key"],
            properties=json.loads(row["properties_json"]) if row["properties_json"] else None,
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
    # Structured data import (CSV/JSON -> container + record documents) -
    # see app/data_import.py for parsing/mapping. Same "blocking, run
    # through AsyncTaskRunner" rule as index_paths above.
    # ------------------------------------------------------------------
    def get_content(self, document_id: int) -> str:
        """Content for the viewer's "open on activate" flow - dispatches on
        `kind` so DocumentsPage/SearchPage can call this uniformly instead
        of assuming every document is a real file on disk (a record's
        `path` is a display artifact, not a file - see migration 0006)."""
        document = self.get(document_id)
        if document is None:
            raise ValueError(f"No such document: {document_id}")

        if document.kind == KIND_CONTAINER:
            row_count = (document.properties or {}).get("row_count", 0)
            return f"This is a container document with {row_count} record(s) - expand it in the Documents tree to browse them."

        if document.kind == KIND_RECORD:
            rows = self._conn.execute(
                "SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index", (document_id,)
            ).fetchall()
            return "\n\n".join(row["text"] for row in rows)

        return extract_text(Path(document.path))

    def import_data_file(
        self,
        profile: Profile,
        path: Path,
        mapping: ImportMapping,
        embed: bool = False,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        force: bool = False,
    ) -> IndexRunSummary:
        """Import a CSV/JSON file as one 'container' document with one
        'record' child per row/object - the structured-data counterpart to
        index_paths(). FTS is available the moment this returns (chunks are
        inserted for every new/changed record regardless of `embed`);
        vector embedding only runs when `embed=True`, batched (see
        _batch_embed) rather than one call per row - see CLAUDE.md's
        "Structured data import" section for why that's opt-in for
        API-backed profiles.

        Row identity/change-detection is keyed by row_key (see
        data_import.resolve_row_key), not by path - a row missing from this
        run that existed on a previous import is removed (see "stale
        cleanup" below), so a shrinking source file doesn't accumulate
        permanent orphans.
        """
        summary = IndexRunSummary()
        path_str = str(path.resolve())
        file_bytes = path.read_bytes()
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        existing_container = self._conn.execute(
            "SELECT * FROM documents WHERE profile_id = ? AND path = ? AND parent_document_id IS NULL",
            (profile.id, path_str),
        ).fetchone()

        unchanged = (
            existing_container is not None
            and existing_container["kind"] == KIND_CONTAINER
            and existing_container["content_hash"] == file_hash
        )
        if unchanged and not force:
            summary.skipped += 1
            return summary

        records = read_records(path)
        container_properties = {
            "format": path.suffix.lower().lstrip("."),
            "content_columns": mapping.content_columns,
            "id_column": mapping.id_column,
            "title_column": mapping.title_column,
            "row_count": len(records),
        }

        if existing_container is not None:
            container_id = existing_container["id"]
            self._conn.execute(
                """
                UPDATE documents
                SET content_hash = ?, size_bytes = ?, indexed_at = datetime('now'),
                    kind = ?, properties_json = ?
                WHERE id = ?
                """,
                (file_hash, len(file_bytes), KIND_CONTAINER, json.dumps(container_properties), container_id),
            )
        else:
            cursor = self._conn.execute(
                """
                INSERT INTO documents
                    (profile_id, path, content_hash, size_bytes, mtime, chunk_count,
                     indexed_at, kind, parent_document_id, properties_json)
                VALUES (?, ?, ?, ?, NULL, 0, datetime('now'), ?, NULL, ?)
                """,
                (profile.id, path_str, file_hash, len(file_bytes), KIND_CONTAINER, json.dumps(container_properties)),
            )
            container_id = cursor.lastrowid
        self._conn.commit()

        # Vector embedding needs an actual vec0 table to write into -
        # silently degrade to FTS-only if this build/profile can't have one,
        # same fallback DocumentRepository already applies everywhere else.
        embed = embed and self._vector_enabled
        vec_table = self._ensure_vec_table(profile) if embed else None
        backend = get_backend(profile) if embed else None

        existing_records = {
            row["row_key"]: row
            for row in self._conn.execute("SELECT * FROM documents WHERE parent_document_id = ?", (container_id,))
        }
        seen_row_keys: Set[str] = set()
        # (record_id, chunk_id, text) awaiting an embedding call - collected
        # across every record before a single batched embedding pass below,
        # rather than one embed() call per row.
        pending_chunks: List[Tuple[int, int, str]] = []

        total = len(records)
        for position, record in enumerate(records, start=1):
            if on_progress:
                on_progress(position, total, path.name)

            row_key = resolve_row_key(record, mapping)
            seen_row_keys.add(row_key)
            content_text = build_record_text(record, mapping)
            content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
            properties_json = json.dumps(record, default=str)

            existing_record = existing_records.get(row_key)
            content_changed = existing_record is None or existing_record["content_hash"] != content_hash

            if not content_changed:
                summary.records_skipped += 1
                # Content is unchanged, but it may still need (re)embedding -
                # e.g. it was imported with embed=False, or the profile's
                # embedding backend/model has changed since it was embedded.
                if embed and (
                    existing_record["embedding_backend"] != profile.embedding_backend
                    or existing_record["embedding_model"] != profile.embedding_model
                ):
                    for chunk_row in self._conn.execute(
                        "SELECT id, text FROM chunks WHERE document_id = ?", (existing_record["id"],)
                    ):
                        pending_chunks.append((existing_record["id"], chunk_row["id"], chunk_row["text"]))
                continue

            text_chunks = chunk_text(content_text, chunk_size=profile.chunk_size)

            if existing_record is not None:
                record_id = existing_record["id"]
                old_chunk_ids = [
                    row["id"] for row in self._conn.execute("SELECT id FROM chunks WHERE document_id = ?", (record_id,))
                ]
                self._conn.execute("DELETE FROM chunks WHERE document_id = ?", (record_id,))
                if old_chunk_ids:
                    self._delete_vec_rows(profile.id, old_chunk_ids)
                self._conn.execute(
                    """
                    UPDATE documents
                    SET content_hash = ?, chunk_count = ?, indexed_at = datetime('now'),
                        embedding_backend = NULL, embedding_model = NULL, properties_json = ?
                    WHERE id = ?
                    """,
                    (content_hash, len(text_chunks), properties_json, record_id),
                )
                summary.records_updated += 1
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO documents
                        (profile_id, path, content_hash, size_bytes, mtime, chunk_count,
                         indexed_at, kind, parent_document_id, row_key, properties_json)
                    VALUES (?, '', ?, 0, NULL, ?, datetime('now'), ?, ?, ?, ?)
                    """,
                    (profile.id, content_hash, len(text_chunks), KIND_RECORD, container_id, row_key, properties_json),
                )
                record_id = cursor.lastrowid
                # `path` only needs to be unique/non-null for the
                # UNIQUE(profile_id, path) constraint - the record's own
                # newly-assigned id guarantees that by construction, unlike
                # row_key (user data - could collide or contain odd
                # characters). Never parsed back apart; see migration 0006.
                self._conn.execute(
                    "UPDATE documents SET path = ? WHERE id = ?",
                    (f"{path_str}::{row_key}#{record_id}", record_id),
                )
                summary.records_created += 1
            self._conn.commit()

            chunk_ids = []
            for chunk in text_chunks:
                cursor = self._conn.execute(
                    """
                    INSERT INTO chunks (document_id, profile_id, chunk_index, text, start_offset, end_offset)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (record_id, profile.id, chunk.index, chunk.text, chunk.start_offset, chunk.end_offset),
                )
                chunk_ids.append(cursor.lastrowid)
            self._conn.commit()

            if embed:
                pending_chunks.extend(
                    (record_id, chunk_id, chunk.text) for chunk_id, chunk in zip(chunk_ids, text_chunks)
                )

        # Stale cleanup: a row_key that existed before this import but isn't
        # in the new file anymore (edited out, or the id column's value
        # changed) - without this, a shrinking source file would accumulate
        # permanent orphans.
        for row_key, stale_row in existing_records.items():
            if row_key not in seen_row_keys:
                self.remove(stale_row["id"])
                summary.records_removed += 1

        if embed and pending_chunks:
            summary.embedded_count = self._batch_embed(profile, backend, vec_table, pending_chunks, on_progress)

        summary.indexed += 1
        return summary

    def embed_records(
        self,
        profile: Profile,
        container_id: int,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> int:
        """Catch-up pass for a "Generate Embeddings" action: embeds every
        record under `container_id` whose stored embedding_backend/model
        don't match the profile's *current* config - covers both records
        imported with embed=False and records embedded under a
        since-changed profile config. Mirrors the same staleness check
        index_paths already relies on for plain files - no extra flag
        needed."""
        if not self._vector_enabled:
            return 0
        vec_table = self._ensure_vec_table(profile)
        backend = get_backend(profile)

        stale_records = self._conn.execute(
            """
            SELECT id FROM documents
            WHERE parent_document_id = ? AND kind = ?
              AND (embedding_backend IS NOT ? OR embedding_model IS NOT ?)
            """,
            (container_id, KIND_RECORD, profile.embedding_backend, profile.embedding_model),
        ).fetchall()

        pending_chunks: List[Tuple[int, int, str]] = []
        for row in stale_records:
            for chunk_row in self._conn.execute("SELECT id, text FROM chunks WHERE document_id = ?", (row["id"],)):
                pending_chunks.append((row["id"], chunk_row["id"], chunk_row["text"]))

        if not pending_chunks:
            return 0
        return self._batch_embed(profile, backend, vec_table, pending_chunks, on_progress)

    def _batch_embed(
        self,
        profile: Profile,
        backend,
        vec_table: str,
        pending_chunks: List[Tuple[int, int, str]],
        on_progress: Optional[Callable[[int, int, str], None]],
    ) -> int:
        """Embed (record_id, chunk_id, text) tuples in fixed-size batches
        (EMBED_BATCH_SIZE) rather than one call per chunk/row, and stamp
        embedding_backend/embedding_model on every touched record once its
        chunks are embedded - that stamp is what lets embed_records() later
        tell which records still need it, without a dedicated flag column.
        `on_progress` fires once per batch (not per row), since this is the
        one place a multi-minute paid-API embed run needs a real progress
        signal rather than a single static status string."""
        total = len(pending_chunks)
        embedded_record_ids: Set[int] = set()
        for start in range(0, total, EMBED_BATCH_SIZE):
            batch = pending_chunks[start : start + EMBED_BATCH_SIZE]
            vectors = backend.embed([text for _record_id, _chunk_id, text in batch])
            for (record_id, chunk_id, _text), vector in zip(batch, vectors):
                self._conn.execute(
                    f"INSERT INTO {vec_table} (rowid, embedding) VALUES (?, ?)",
                    (chunk_id, serialize_vector(vector)),
                )
                embedded_record_ids.add(record_id)
            self._conn.commit()
            if on_progress:
                on_progress(min(start + EMBED_BATCH_SIZE, total), total, "embedding")

        if embedded_record_ids:
            placeholders = ",".join("?" for _ in embedded_record_ids)
            self._conn.execute(
                f"UPDATE documents SET embedding_backend = ?, embedding_model = ? WHERE id IN ({placeholders})",
                (profile.embedding_backend, profile.embedding_model, *embedded_record_ids),
            )
            self._conn.commit()
        return len(embedded_record_ids)

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
    are ordered by score descending, so its best-matching chunk is always
    first - both for the results list's "best snippet" column and for the
    table of contents/navigation in DocumentViewerPanel. Documents
    themselves are also ordered by their best chunk's score, matching the
    overall relevance order hybrid_search() already produced."""
    by_document: Dict[int, List[SearchResult]] = {}
    for result in results:
        by_document.setdefault(result.document_id, []).append(result)

    grouped = []
    for document_id, matches in by_document.items():
        matches_by_score = sorted(matches, key=lambda m: m.score, reverse=True)
        grouped.append(
            DocumentSearchResult(
                document_id=document_id,
                document_path=matches[0].document_path,
                score=matches_by_score[0].score,
                matches=matches_by_score,
            )
        )
    grouped.sort(key=lambda doc: doc.score, reverse=True)
    return grouped


def _snippet(text: str, max_length: int = SNIPPET_MAX_LENGTH) -> str:
    flattened = " ".join(text.split())
    if len(flattened) <= max_length:
        return flattened
    return flattened[:max_length].rstrip() + "…"
