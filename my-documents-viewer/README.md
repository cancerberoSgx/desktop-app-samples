# My Documents Viewer

A desktop app, built with [wxPython](https://wxpython.org), for indexing local text
files (`.txt`, `.md` for now) and finding the most relevant passages later via
full-text search, vector similarity search, or a hybrid of both.

## Project layout

```
main.py                       Entry point
app/
  frame.py                    Main window: menu bar + sidebar + page-switching area
  sidebar.py                  Left navigation sidebar with icon buttons
  pages.py                    About page
  profiles_page.py            "Profiles" screen: list + CRUD toolbar + Activate
  profiles_dialog.py          Create/edit form for a profile (name + embedding config)
  documents_page.py           "Documents" screen: add files/folders, list, (re)index, remove
  search_page.py              "Search" screen: hybrid / full-text / vector query
  models.py                   Profile / Document / SearchResult dataclasses
  repositories.py             ProfileRepository / SettingsRepository / DocumentRepository
                               (pure SQL against SQLite; DocumentRepository does indexing,
                               chunking, embedding, and hybrid search)
  chunking.py                 Splits document text into overlapping chunks
  text_extract.py             Plain-text extraction for .txt/.md (future formats plug in here)
  vector_codec.py             Packs Python float lists into sqlite-vec's blob format
  embeddings/
    base.py                   EmbeddingBackend interface + EmbeddingError
    registry.py                Catalog of offered models (backend, name, dimension)
    fastembed_backend.py       Local ONNX embeddings via fastembed (default, no API key)
    openai_backend.py          OpenAI Embeddings API over plain HTTPS (API key required)
    gemini_backend.py          Gemini batchEmbedContents API over plain HTTPS (API key required)
  db/
    paths.py                  Resolves ~/.my-documents-viewer and the migrations folder
    connection.py              SQLite connection factory + sqlite-vec extension loading
    migrator.py                 Applies any new *.sql file under db/migrations/
    migrations/
      0001_create_profiles.sql
      0002_create_documents.sql
      0003_create_chunks.sql    (chunks table + FTS5 virtual table + sync triggers)
      0004_create_settings.sql
probes/
  index_and_search.py          Standalone fastembed + sqlite-vec quality check (no UI)
requirements.txt
mydocumentsviewer.spec
```

## Data storage

App data lives in a SQLite database at `~/.my-documents-viewer/my-documents-viewer.db`,
created on first run. Schema changes are made by adding a new numbered `.sql` file
under `app/db/migrations/` - `run_migrations()` applies any file not yet recorded in
the `schema_migrations` table, in filename order.

Full-text search uses a single shared FTS5 table (`chunks_fts`). Vector search uses a
**per-profile** [sqlite-vec](https://github.com/asg017/sqlite-vec) `vec0` virtual
table (`vec_chunks_<profile_id>`), created on demand - it can't be a single shared
table because its column width is fixed at creation time to the profile's chosen
embedding model's dimension.

## Profiles

A profile is a document "kind" you define - e.g. "History", "Development",
"Contracts" - and everything (documents, chunks, embeddings) is scoped to one. Each
profile independently picks:

- an **embedding backend**: `fastembed` (local, default, no API key), `openai`, or
  `gemini` (both require an API key, entered on the profile),
- an **embedding model**, which determines the **vector dimension** used for that
  profile's `vec0` table.

On first run a `"default"` profile is created automatically (fastembed / 384d).
Changing a profile's embedding backend/model resets (drops) its vector index -
existing documents stay full-text searchable, but need **Reindex All** (Documents
screen) to be searchable by similarity again, since old and new embeddings aren't
comparable.

## Documents

From the "Documents" screen, **Add Files...**/**Add Folder...** index `.txt`/`.md`
files (folders are walked recursively). Indexing:

1. reads the file as UTF-8 text,
2. splits it into overlapping ~800-character chunks (`app/chunking.py`),
3. stores the chunks (both in the plain `chunks` table and, via triggers, the
   `chunks_fts` FTS5 index),
4. embeds each chunk with the profile's configured backend and stores the vectors in
   that profile's `vec0` table.

A file whose content hash and embedding backend/model haven't changed since its last
index is skipped automatically; **Reindex Selected**/**Reindex All** force a
re-embed (needed after switching a profile's embedding model). **Remove** deletes a
document (and its chunks/embeddings) from the index - it never touches the file on
disk.

Indexing runs through `AsyncTaskRunner` (`app/async_task.py`) so embedding calls
(local ONNX inference, or a network round-trip for OpenAI/Gemini) never freeze the
window.

## Search

The "Search" screen queries the active profile in one of three modes:

- **Hybrid** (default): full-text (FTS5, ranked by bm25) and vector similarity
  (sqlite-vec, ranked by distance) are both run, then combined via **Reciprocal
  Rank Fusion** - this tends to beat either alone, since keyword search catches
  exact terms (names, identifiers) that embeddings fuzz over, and vector search
  catches paraphrases/synonyms keyword search misses.
- **Full-text only** - bm25-ranked FTS5.
- **Vector only** - nearest neighbors by embedding distance; disabled in the UI if
  vector search isn't available on this build (see below).

## When vector search isn't available

`sqlite-vec` is a loadable SQLite extension; a small number of Python builds (some
distro-packaged or Windows Store Python) don't support loading extensions at all
(`sqlite3.Connection.enable_load_extension` is missing). The app detects this once
at startup (`app/db/connection.vector_search_available`) and degrades gracefully:
full-text search keeps working, "Vector only" is disabled in the Search screen, and
the status bar/About screen say so.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The default `fastembed` backend downloads its model file (tens to ~200 MB) on first
use, cached under `~/.my-documents-viewer/fastembed-models`.

## Run

```bash
python3 main.py
```

## Probes

`probes/index_and_search.py` is a small standalone script (no wxPython, no on-disk
database) that indexes a handful of sample texts with fastembed and searches them
with sqlite-vec, to sanity-check retrieval quality in isolation. See
`probes/README.md`.

## Building standalone executables

Standalone executables are built with [PyInstaller](https://pyinstaller.org).
PyInstaller does not cross-compile, so each executable must be built on its target
OS (build the Linux binary on Linux, the Windows `.exe` on Windows, and the macOS
`.app` on macOS).

Install PyInstaller into the same virtual environment used to run the app:

```bash
pip install pyinstaller
```

```bash
pyinstaller --noconfirm mydocumentsviewer.spec
```

The spec file bundles `app/db/migrations/*.sql` and `sqlite-vec`'s native extension
file as data (see `mydocumentsviewer.spec`'s use of `collect_data_files`) so both
migrations and vector search work from a packaged build too. `fastembed`'s ONNX
runtime dependency has not been verified against a PyInstaller build in this repo
yet - if it needs extra hidden imports/hooks, add them to `mydocumentsviewer.spec`
the same way.

The executable is created at `dist/mydocumentsviewer/mydocumentsviewer` (`.exe` on
Windows, `dist/mydocumentsviewer.app` plus `dist/mydocumentsviewer/` on macOS).
Distribute the whole `dist/mydocumentsviewer/` folder, not just the executable - it
depends on the other files placed alongside it (GTK must be present on the target
Linux machine; on macOS, an unsigned app must be right-clicked > Open to bypass
Gatekeeper, or signed/notarized for distribution).
