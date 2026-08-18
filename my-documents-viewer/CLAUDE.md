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

Alongside plain files, a profile can also **import a structured data file**
(CSV or JSON array-of-objects) as one *container* document with one *record*
child document per row/object - e.g. a product catalog searchable by SKU. See
"Structured data import" below.

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

- **CRUD**: `list_top_level`/`list_children`/`get`/`remove` for documents, scoped by
  `profile_id`. `list_top_level` returns plain files and containers (not their
  record children - see "Structured data import" below); `remove` on a container
  removes its whole subtree.
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

### Structured data import (CSV/JSON -> container + record documents)

`Document.kind` (migration `0006_add_document_hierarchy.sql`) is `'file'` (the
default - today's one-document-per-source-file case, unchanged), `'container'`
(a CSV/JSON source file, no content/chunks of its own), or `'record'` (one row/
object under a container, via `parent_document_id`). `app/data_import.py` parses
CSV (`csv.DictReader`) and JSON (top-level array of objects only) into plain
dicts; `app/data_import_dialog.py::ImportMappingDialog` lets the user choose
which columns become searchable content vs. display-only metadata, and which
column (if any) stably identifies a row across re-imports (falls back to a
content hash, never a positional index - see `data_import.resolve_row_key`'s
docstring for why). `DocumentRepository.import_data_file` does the actual
upsert/diff/stale-cleanup (a row_key missing from a re-import is removed, not
left orphaned) and is the structured-data counterpart to `index_paths` - same
"always invoke through `AsyncTaskRunner`" rule applies.

**Embedding every row is opt-in, not automatic.** FTS indexing happens
immediately and unconditionally on import (chunks sync into `chunks_fts` the
same way any other chunk does); vector embedding is a separate, batched stage
(`EMBED_BATCH_SIZE` rows per `embed()` call, not one call per row) that only
runs when the caller passes `embed=True`. `DocumentsPage` auto-embeds for a
`fastembed` profile (local/free - only a time cost, shown via a progress
gauge) but always asks first for `openai`/`gemini` profiles (a 3-way dialog:
generate now / full-text-only for now / cancel) - importing a large catalog
against a paid API must never fire one HTTP call per row as a surprise side
effect of clicking "Import". A record left un-embedded (or embedded under a
since-changed profile config) can be caught up later via `embed_records()`
("Generate Embeddings" on a container) - it's found by comparing the record's
stored `embedding_backend`/`embedding_model` against the profile's *current*
config, the same staleness check `index_paths` already relies on for plain
files, so no extra flag column was needed.

A record's `path` column is a display/storage artifact only (made unique by
appending the record's own row id), never parsed back apart - identity for
diffing/lookup goes through the dedicated `idx_documents_row_key` index
(`profile_id, parent_document_id, row_key`) instead. `DocumentsPage` shows the
hierarchy as a `wx.dataview.TreeListCtrl` (not `wx.ListCtrl` - it can't show a
tree), lazily populating a container's records on first expand via the same
dummy-placeholder-child pattern `my-redis-viewer`'s `KeyTreeView` uses.
`DocumentRepository.get_content(document_id)` is what both `DocumentsPage` and
`SearchPage` use to open a document in the viewer - it dispatches on `kind`
(`extract_text` for a file, concatenated chunks for a record) - **never call
`extract_text` directly on a document's `path`** the way both pages once did,
since a record's path isn't a real file. It has no `kind == 'container'` case
though - a container has no content/chunks of its own, so `DocumentsPage`
double-clicking one doesn't call `get_content` at all; it calls
`list_children` and hands the records straight to
`DocumentViewerPanel.show_records`, which lists them as a flat data grid
(columns = the union of keys across every record's raw `properties`, in
first-seen order - i.e. the source file's own column order) rather than
showing a placeholder in the text view. `app/file_display.py::format_document_label`
is the display-label helper (`container › row`) used everywhere a document's
`path` would otherwise leak into UI text (viewer titles, search results, the
tree); `format_record_short_label` is the same thing minus the container
prefix, for contexts (SearchPage's results tree) where the record is already
shown visually nested under its container.

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
are `DocumentRepository.index_paths`/`import_data_file`/`embed_records` (file I/O
+ embedding, called from `DocumentsPage`) and `DocumentRepository.hybrid_search`
(an embedding call for the query, called from `SearchPage`) - all run through
`self._async.run(...)`, never directly from a button handler. Each of the three
`DocumentsPage`-side calls takes an `on_progress(done, total, label)` callback
invoked *from the worker thread* - callers must hop back to the UI thread
themselves (`wx.CallAfter(...)`) inside that callback, same as any other
cross-thread wx update; see `DocumentsPage._start_indexing`/`_start_import` for
the reference usage. Because `AsyncTaskRunner` silently ignores an overlapping
`.run()` call rather than queueing it (see its docstring), `DocumentsPage`
chains multiple sequential runs (e.g. "Reindex All" across a mix of plain files
and containers, each container needing its own embedding-consent decision) via
an explicit `on_done_extra` callback rather than firing them back to back - see
`_run_steps`.

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
(2), About (3). `profiles_page.py`/`profiles_dialog.py` follow the same
list+toolbar-on-`wx.ListCtrl` / modal-dialog-for-create-edit pattern as
`my-redis-viewer`'s pages - see that project's CLAUDE.md for the pattern to
follow when adding a new concept's screen. `documents_page.py` and
`search_page.py` are both `wx.dataview.TreeListCtrl` instead, since a
container's records (see "Structured data import" above) need to show up
nested under it in both places - `SetItemData`/`GetItemData` round-trip plain
Python objects directly in this wxPython version (Phoenix 4.3.1+; no
`ClientData` wrapping needed). The two pages build their tree differently
though, matching how differently-sized their child sets are:
`DocumentsPage` **lazily** loads a container's records on first expand (same
dummy-placeholder-child trick as `my-redis-viewer`'s `KeyTreeView`,
`app/data_explorer_page.py`) since a container can hold thousands of them;
`SearchPage` builds its whole tree **eagerly** every search
(`SearchPage._build_result_groups`), since a result set is small and already
fully in hand - a container only appears as a top-level row at all if one of
its records matched, grouping every matching record under it (score, match
count, and best snippet aggregated from its best-scoring child, same
"ordered by best chunk's score" rule `repositories.group_by_document` already
applies at the single-document level) rather than listing records as
unrelated flat rows the way the pre-hierarchy version did.

### Find-in-text (`DocumentViewerPanel`)

Separate from the chunk-match Prev/Next nav (which jumps between *recorded*
search-hit offsets): a find bar, toggled by the header's magnifying-glass
button or Ctrl+F (F3/Shift+F3 for next/prev), searches the raw Scintilla text
itself via `StyledTextCtrl.FindText` - case-insensitive substring, no regex
(`flags=0`). Since `DocumentViewerFrame` is the one class both `DocumentsPage`
and `SearchPage` construct their viewer from, this - like everything else in
`document_viewer.py` - is automatically available from both. The one thing to
preserve if touching `_run_find`: it always searches from the *current*
selection/caret position, not from the top of the document, so repeated
Next/Prev continues in the direction the user's already reading rather than
fighting their scroll position - `FindText`'s positions are already
Scintilla's native byte offsets (confirmed empirically: it returns a
`(start, end)` tuple directly usable with `SetSelection`/`ScrollRange`, no
`_char_to_byte` conversion needed the way chunk-highlighting requires, since
the query string and the buffer are both being measured in the same units by
Scintilla itself here). Ctrl+F is wired via an `AcceleratorTable` on
`DocumentViewerFrame` (not a plain key binding on one widget), since it needs
to fire regardless of which child control currently has focus. Find is
unavailable (button disabled, bar force-hidden) while
`DocumentViewerPanel.show_records` is showing a container's record grid
instead of text - see `_set_content_mode`.

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
