"""Repository/service-layer integration test - no wx UI involved.

Exercises the same path DocumentsPage/SearchPage drive through
AsyncTaskRunner: create a profile (default fastembed config, untouched -
embedding is opt-in per CLAUDE.md's "Structured data import" section, so
this stays hermetic/fast with no model download), import two one-row CSV
files as container+record documents, then confirm full-text search finds
the right document(s). The db lives in a per-test temp file (not the real
~/.my-documents-viewer/my-documents-viewer.db a running app would use), and
the fixture explicitly deletes the profile it created - which cascades to
its documents/chunks/chunks_fts rows (see the FK ON DELETE CASCADE chain
documented on DocumentRepository.remove) - leaving nothing behind either way.
"""
import sqlite3
from pathlib import Path

import pytest

from app.data_import import ImportMapping
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.repositories import DocumentRepository, ProfileRepository, group_by_document


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "documents.db")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    run_migrations(connection, migrations_dir())
    yield connection
    connection.close()


@pytest.fixture
def profile_repo(conn):
    return ProfileRepository(conn)


@pytest.fixture
def doc_repo(conn):
    # vector_enabled=False: this test only exercises full-text search, so no
    # sqlite-vec extension/vec0 table is needed - see CLAUDE.md's "Vector
    # search availability is a soft, whole-app flag".
    return DocumentRepository(conn, vector_enabled=False)


def _write_csv(path: Path, title: str, description: str) -> None:
    path.write_text(f"title,description\n{title},{description}\n", encoding="utf-8")


def test_fulltext_search_finds_expected_csv_documents(tmp_path, profile_repo, doc_repo):
    profile = profile_repo.create("csv-search-test")
    assert profile.embedding_backend == "fastembed"  # default local embedding backend

    try:
        a_path = tmp_path / "a.csv"
        b_path = tmp_path / "b.csv"
        _write_csv(a_path, title="foo", description="lorem ipsum")
        _write_csv(b_path, title="bar", description="lorem ipsum")

        mapping = ImportMapping(content_columns=["title", "description"], id_column="title")

        summary_a = doc_repo.import_data_file(profile, a_path, mapping, embed=False)
        summary_b = doc_repo.import_data_file(profile, b_path, mapping, embed=False)
        assert summary_a.records_created == 1
        assert summary_b.records_created == 1

        containers = doc_repo.list_top_level(profile.id)
        a_container = next(doc for doc in containers if doc.path.endswith("a.csv"))
        b_container = next(doc for doc in containers if doc.path.endswith("b.csv"))

        def matched_containers(query: str) -> set:
            results = doc_repo.hybrid_search(profile, query, mode="fulltext")
            grouped = group_by_document(results)
            return {doc_repo.get(result.document_id).parent_document_id for result in grouped}

        assert matched_containers("lorem ipsum") == {a_container.id, b_container.id}
        assert matched_containers("bar") == {b_container.id}
    finally:
        profile_repo.delete(profile.id)
        # ON DELETE CASCADE (profiles -> documents -> chunks -> chunks_fts,
        # see migrations 0002/0003/0006 and DocumentRepository.remove's
        # docstring) should have taken every record/container/chunk with
        # it - confirm nothing was left behind, not just the top-level view.
        conn = doc_repo._conn  # noqa: SLF001 - direct check, test-only
        assert profile_repo.get(profile.id) is None
        assert doc_repo.list_top_level(profile.id) == []
        remaining_documents = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE profile_id = ?", (profile.id,)
        ).fetchone()[0]
        remaining_chunks = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE profile_id = ?", (profile.id,)
        ).fetchone()[0]
        assert remaining_documents == 0
        assert remaining_chunks == 0
