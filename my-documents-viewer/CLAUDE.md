# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app for indexing local text files (`.txt`/`.md` for now) and
searching them later by keyword, vector similarity, or both. Users pick a
**profile** (a document "kind", e.g. "History"/"Development"/"Contracts") - every
document, chunk, and embedding belongs to one. Each profile independently chooses
an embedding backend (`fastembed` by default - local, no API key; `openai`/`gemini`
as optional API-key-based alternatives) and model, which determines the vector
dimension used for that profile's search index.

This project was templated from the sibling `my-redis-viewer` app - same overall
composition-root/repository-pattern/migrations/profiles-scope-everything
architecture, sidebar, and `AsyncTaskRunner` pattern, but with Redis connections
replaced by local document indexing + hybrid search (see `prompts.md` at the repo
root's "my-documents view" section for the original design discussion this was
built from).

## Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python3 main.py

# Quality-check the embedding/vector-search stack in isolation (no UI)
python3 probes/index_and_search.py

# Build a standalone executable (Linux/Windows/macOS - must build on the target OS)
.venv/bin/pip install pyinstaller     # once, into THIS project's venv
.venv/bin/pyinstaller --noconfirm mydocumentsviewer.spec
./dist/mydocumentsviewer/mydocumentsviewer    # the runnable output
```

There is no linter or test suite configured in this repo yet.

## Architecture

### Startup wiring (`app/frame.py`)

`MainFrame.__init__` does all composition-root work: opens the single sqlite3
connection (`app/db/connection.py`, which also loads the sqlite-vec extension),
runs pending migrations, probes whether vector search actually came up
(`vector_search_available(conn)`), builds the repositories on top of that one
connection (threading the vector-availability flag into `DocumentRepository`),
then resolves which profile is active before building any page. There is no
dependency injection framework - everything is wired by hand in this one place.

### Profiles scope everything

Same pattern as `my-redis-viewer`: `MainFrame._bootstrap_active_profile()` creates
a `"default"` profile on a cold start, restores the last-active profile id from
`settings`, and `_on_profiles_changed()`/`_on_activate_profile()` keep the active
profile valid and propagate a switch to every page (`DocumentsPage.set_profile()`,
`SearchPage.set_profile()`). See `my-redis-viewer/CLAUDE.md` for the fuller
rationale if extending this further - it hasn't changed here.

**New here:** a profile also carries its embedding config (`embedding_backend`,
`embedding_model`, `embedding_dim`, `openai_api_key`, `gemini_api_key`). Editing
these on an existing profile (`ProfilesPage._on_edit`) detects a change and calls
`DocumentRepository.reset_vector_index(profile_id)`, which drops that profile's
`vec0` table - old and new embeddings live in different vector spaces and can't be
compared, so the old table has to go. Documents stay full-text searchable in the
meantime; the user is told to run "Reindex All" to restore vector/hybrid search.

### Repository pattern

`ProfileRepository`/`SettingsRepository` are pure CRUD, same shape as
`my-redis-viewer`'s. `DocumentRepository` (`app/repositories.py`) is the one with
real logic:

- **CRUD**: `list`/`get`/`remove` for documents, scoped by `profile_id`.
- **Indexing** (`index_paths`): reads a file, hashes its content, chunks it
  (`app/chunking.py`), stores chunks (which sync into the shared `chunks_fts` FTS5
  table via triggers - see migration `0003_create_chunks.sql`), embeds each chunk
  via `embeddings.get_backend(profile)`, and inserts the vectors into that
  profile's `vec_chunks_<profile_id>` table. Skips a file whose content hash *and*
  embedding backend/model match its last index, unless `force=True` (used by
  Reindex Selected/All). Runs entirely on the calling thread - **always invoke
  through `AsyncTaskRunner`** from a page (see below), never directly from a
  `wx.EVT_*` handler; embedding a batch of chunks can take real time, especially
  against a network API.
- **Search** (`hybrid_search`): runs FTS5 (bm25) and/or sqlite-vec (distance)
  depending on `mode`, then combines both with Reciprocal Rank Fusion
  (`score = sum(1/(RRF_K + rank))` across whichever rankings a chunk appears in) -
  see the docstring on `hybrid_search` for the exact rules, especially how
  `"hybrid"` mode silently falls back to full-text-only if the embedding call
  fails (e.g. bad API key), while `"vector"` mode raises instead.

### Per-profile sqlite-vec tables (not a static migration)

Vector dimension is chosen per profile (it depends on the embedding model), so it
can't be baked into a fixed migration file the way `chunks`/`chunks_fts` are.
Instead, `DocumentRepository._ensure_vec_table(profile)` does
`CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks_<profile_id> USING vec0(embedding
float[<dim>])` lazily, right before it's needed (first index, or a search).
**Any code that touches vectors for a profile must go through this method (or
`_vec_table(profile_id)` for the name) rather than assuming the table exists** -
a profile that has never been indexed with vectors on, or whose vector index was
just reset, legitimately has no such table yet, and every vector code path here is
wrapped in `try`/`except sqlite3.OperationalError` for exactly that reason.

### Vector search availability is a soft, whole-app flag

`app/db/connection.py` tries to load the `sqlite-vec` extension on every
connection; `vector_search_available(conn)` then does a real check
(`SELECT vec_version()`) once at startup, and that boolean is threaded into
`DocumentRepository(vector_enabled=...)`. **Never assume vector search is
available** - `sqlite-vec` might not be installed, or (rarer) this Python build
might not support loadable extensions at all
(`enable_load_extension` missing → `AttributeError`, not `ImportError` - both are
caught). When `vector_enabled` is False: `DocumentRepository` skips every vec0
code path (indexing stores no vectors, `hybrid_search` behaves as full-text-only),
`SearchPage` disables the "Vector only" radio option, and the About page/status
bar say so. Do not add a vector code path that isn't guarded by this flag.

### Embeddings backends (`app/embeddings/`)

One `EmbeddingBackend` (`base.py`) per provider - `FastEmbedBackend` (local ONNX
via the `fastembed` package, lazy-loads the model on first `embed()` call so
importing this module never pays that cost), `OpenAIEmbeddingBackend`,
`GeminiEmbeddingBackend` (both plain `urllib` HTTPS calls, deliberately with no
SDK dependency - they're optional backends, and the raw REST call is a handful of
lines). `embeddings.get_backend(profile)` is the only place that dispatches on
`profile.embedding_backend` - add a new backend there and in `registry.py`'s
`EMBEDDING_MODELS` catalog (which is also what `ProfileDialog` reads to populate
its model dropdown and dimension label), not by branching elsewhere.
All backends raise `EmbeddingError` (never a provider-specific exception type) on
any failure - missing dependency, missing/invalid API key, network/API error -
so callers only ever need to catch one exception type.

### Blocking calls must go through `AsyncTaskRunner` (`app/async_task.py`)

Unchanged from `my-redis-viewer` - see that project's CLAUDE.md for the full
rationale (thread + `wx.CallAfter`, not asyncio/wxasync). Here, the blocking calls
are `DocumentRepository.index_paths` (file I/O + embedding, called from
`DocumentsPage`) and `DocumentRepository.hybrid_search` (an embedding call for the
query, called from `SearchPage`) - both run through `self._async.run(...)`, never
directly from a button handler. `index_paths` additionally takes an
`on_progress(done, total, path)` callback invoked *from the worker thread* -
callers must hop back to the UI thread themselves (`wx.CallAfter(...)`) inside
that callback, same as any other cross-thread wx update; see
`DocumentsPage._start_indexing` for the reference usage.

### Migrations (`app/db/migrations/*.sql`)

Same mechanism as `my-redis-viewer`: add a new numbered `.sql` file, never edit an
already-applied one. `0003_create_chunks.sql` is worth reading before touching
`chunks`/`chunks_fts` - it sets up the FTS5 external-content table and the three
sync triggers (`chunks_ai`/`chunks_ad`/`chunks_au`) that keep it in step with the
`chunks` table; any new way of writing to `chunks` must go through normal
INSERT/UPDATE/DELETE (not e.g. a bulk `executescript` that bypasses row-level
triggers) or the FTS index will drift out of sync. Remember per-profile `vec0`
tables are *not* part of this migration system at all (see above).

### UI structure

Left `Sidebar` (`app/sidebar.py`) drives a `wx.Simplebook` in `MainFrame` -
`SIDEBAR_ITEMS` order must match page order: Profiles (0), Documents (1), Search
(2), About (3). `profiles_page.py`/`profiles_dialog.py`,
`documents_page.py`, `search_page.py` follow the same
list+toolbar-on-`wx.ListCtrl` / modal-dialog-for-create-edit pattern as
`my-redis-viewer`'s pages - see that project's CLAUDE.md for the pattern to
follow when adding a new concept's screen.

### PyInstaller packaging gotchas

- **Always build with this project's own venv's pyinstaller**
  (`.venv/bin/pyinstaller`), never a bare `pyinstaller` resolved from `PATH`.
- **Build via `pyinstaller --noconfirm mydocumentsviewer.spec`**, not
  `pyinstaller ... main.py` - the latter regenerates the spec from scratch,
  silently wiping both the migrations `datas` entry and the
  `collect_data_files('sqlite_vec')` call that bundles sqlite-vec's native
  extension file.
- **`fastembed`'s onnxruntime dependency has not been verified against a
  PyInstaller build in this repo** - unlike the migrations/sqlite-vec bundling
  above, this hasn't actually been tested end-to-end; if a packaged build fails to
  load a fastembed model, start by checking for missing hidden imports/binaries
  from `onnxruntime` and add them to `mydocumentsviewer.spec`.
- **The runnable output is `dist/mydocumentsviewer/mydocumentsviewer`.** `build/`
  is PyInstaller's intermediate scratch directory and is never a complete,
  runnable tree.
