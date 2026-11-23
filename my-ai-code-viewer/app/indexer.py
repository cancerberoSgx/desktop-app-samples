"""Sync providers -> the app's own SQLite archive.

Runs on a worker thread (see ProjectsPage._start_sync) with its own
SQLite connection. Incremental: a conversation is only re-parsed when its
provider-reported fingerprint (file mtime+size, or OpenCode's
session.time_updated) differs from the stored one, so after the first run a
sync is just a directory listing plus a few changed files.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from app.db.connection import get_connection
from app.providers.base import ProviderAdapter
from app.repositories import IndexRepository


@dataclass
class SyncResult:
    scanned: int = 0
    updated: int = 0
    missing: int = 0
    errors: List[str] = field(default_factory=list)


def sync(repo: IndexRepository, providers: List[ProviderAdapter],
         progress: Optional[Callable[[str], None]] = None) -> SyncResult:
    result = SyncResult()
    for provider in providers:
        if not provider.is_available():
            continue
        try:
            sources = provider.list_sources()
        except Exception as exc:  # noqa: BLE001 - one broken provider mustn't stop the others
            result.errors.append(f"{provider.label}: {exc}")
            continue
        known = repo.fingerprints(provider.id)
        result.scanned += len(sources)
        changed = [s for s in sources if known.get(s.external_id) != s.fingerprint]
        for i, source in enumerate(changed, 1):
            if progress:
                progress(f"{provider.label}: indexing {i}/{len(changed)}")
            try:
                conv = provider.parse(source)
            except Exception as exc:  # noqa: BLE001 - skip one unreadable transcript
                result.errors.append(f"{source.source_path}: {exc}")
                continue
            project_id = repo.upsert_project(provider.id, source.project_key, source.project_path)
            if conv.project_path and conv.project_path != source.project_path:
                repo.set_project_path(project_id, conv.project_path)
            repo.save_conversation(project_id, provider.id, source.source_path, source.fingerprint, conv)
            repo.commit()
            result.updated += 1
        result.missing += repo.mark_missing(provider.id, (s.external_id for s in sources))
        repo.refresh_project_activity()
        repo.commit()
    return result


def sync_in_new_connection(providers: List[ProviderAdapter], db_file: Optional[Path] = None,
                           progress: Optional[Callable[[str], None]] = None) -> SyncResult:
    """Entry point for the worker thread: sqlite3 connections can't cross
    threads, so the sync gets a connection of its own."""
    conn = get_connection(db_file)
    try:
        return sync(IndexRepository(conn), providers, progress)
    finally:
        conn.close()
