# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app: a performant local file explorer. Pin folders as
**favorites** in a collapsible left sidebar, open a folder to see its immediate
contents in a sortable table (Name, Size, Modified), and pick up where you left
off - the last folder open is remembered in preferences, same as whether the
sidebar was collapsed.

This project was templated from the sibling `my-redis-viewer` app for its overall
architecture (SQLite + migrations under `app/db/`, `AsyncTaskRunner` facade for
running blocking calls off the UI thread, repository-per-concept pattern,
collapsible sidebar, preferences/settings table) - but **profiles and datasources
were intentionally not carried over**: this is a single local file explorer, not
a multi-connection tool. `app/async_task.py` is copied verbatim (generic
infrastructure, nothing filesystem-specific in it) - same precedent as
`my-disk-viewer` carrying it over unchanged from `my-docker-viewer`.

## Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python3 main.py

# Build a standalone executable (Linux/Windows/macOS - must build on the target OS)
.venv/bin/pip install pyinstaller     # once, into THIS project's venv
.venv/bin/pyinstaller --noconfirm myfileviewer.spec
./dist/myfileviewer/myfileviewer      # the runnable output - see packaging gotchas below
```

There is no linter or test suite configured in this repo yet.

## Architecture

### Startup wiring (`app/frame.py`)

`MainFrame.__init__` is the composition root: opens the single sqlite3 connection
(`app/db/connection.py`), runs pending migrations (`app/db/migrator.py`), builds
`FavoriteRepository`/`SettingsRepository` on top of that one connection plus the
stateless `FileSystemService`, then builds the sidebar + explorer page. There is
no dependency injection framework - everything is wired by hand in this one
place, same as `my-redis-viewer`. `_restore_last_folder()` runs at the end of
`__init__`, reopening whichever folder `SettingsRepository.get_last_folder_path()`
returns (falling back to the user's home directory if that path no longer exists
or on first run).

### No wx.Simplebook / page-switching sidebar

Unlike `my-redis-viewer`'s `Sidebar`, which drives a `wx.Simplebook` between a
fixed set of pages (Profiles/Data Sources/About), `FavoritesSidebar` doesn't
switch pages - there's only one main screen, `FolderExplorerPage`. The sidebar's
job is a dynamic list of favorite-folder shortcuts: clicking one calls
`FolderExplorerPage.open_folder(path)` on the same, single page instance. "About"
is a plain `wx.MessageBox` off the Help menu instead of a dedicated page, since
there was nothing else worth a whole screen for it.

### Repository pattern

One `<Concept>Repository` class per concept (`FavoriteRepository`,
`SettingsRepository`, both in `app/repositories.py`), each doing plain SQL
against the shared `sqlite3.Connection` (row_factory is `sqlite3.Row`) - the
same convention as `my-redis-viewer`. `FavoriteRepository.add_folder` is
idempotent (favoriting an already-favorited path returns the existing row rather
than raising the `path` column's UNIQUE constraint violation) since the toolbar
button toggles Add/Remove based on membership, not an explicit "already a
favorite" error path.

### The "service" pattern: `FileSystemService` (`app/file_system_service.py`)

This is the concept the human's spec explicitly asked to keep obvious for future
extension: **every folder action lives in `FileSystemService`, as a plain
blocking method, and every call site invokes it through `AsyncTaskRunner`
(`app/async_task.py`), never directly from a `wx.EVT_*` handler.** Today it has
one method, `list_folder(path)`; every future action (rename, delete, copy/move,
create folder, recursive folder size, glob-based search, more file
details/columns) belongs here as its own method, called the same way -
`FolderExplorerPage._load_current_folder` is the reference usage:

```python
self._async.run(
    work=lambda: self._file_service.list_folder(path),
    on_success=lambda listing: self._on_folder_loaded(path, listing),
    on_error=lambda exc: self._on_folder_load_error(str(exc)),
    on_done=self._on_load_done,
    disable=[self._open_btn, self._up_btn],
)
```

`FileSystemService` is stateless (no sqlite connection, no cache) - unlike
`FavoriteRepository`/`SettingsRepository`, it only ever talks to the OS
filesystem, so it needs nothing injected beyond the arguments each call takes.

### Blocking filesystem calls must go through `AsyncTaskRunner` (copied from `my-redis-viewer`)

Even a "cheap" `os.scandir`/`os.stat` pass can stall for seconds on a
network-mounted or otherwise slow folder, and future actions (recursive size,
glob search across a large tree) will be much more expensive - so **every
`FileSystemService` method is invoked through `AsyncTaskRunner`, never called
synchronously.** This is the same facade and the same rules as
`my-redis-viewer`'s (see that project's CLAUDE.md for the original rationale):
one instance per page (`self._async = AsyncTaskRunner(self)`), a second `run()`
call while one is in flight is ignored (no re-entrancy guard needed on top),
`disable=[...]` should list the triggering widgets, and callbacks
(`on_success`/`on_error`/`on_done`) always land back on the UI thread via
`wx.CallAfter` under the hood - so it's safe to touch widgets and the sqlite
connection from inside them, but never from inside `work`.

- **Stale-result guard**: every `on_success` callback that touches shared state
  first checks the path/id it was called for still matches what's currently
  open (`if path != self._current_path: return` in
  `FolderExplorerPage._on_folder_loaded`) - guards against a slow background
  listing landing after the user already navigated elsewhere.

### Folder contents table (`app/folder_contents_ctrl.py`)

`FolderContentsCtrl` is a virtual `wx.ListCtrl` (`LC_VIRTUAL`) - no per-row wx
item is ever created, so it stays responsive on a folder with a very large
number of entries; only `OnGetItemText` is called, and only for rows currently
on screen. Three columns today (Name, Size, Modified); more (file type, glob
match, recursive size, ...) are meant to be added to `_COLUMNS`/`_SORT_KEYS`
alongside `FileEntry` gaining the backing field. Clicking a header sorts by it,
click again to reverse.

- **Folders always sort before files**, regardless of the active column or
  direction - the usual file-explorer convention. This is implemented as **two
  stable sorts**, not one sort keyed on `(group, value)`: first by the column's
  key (honoring `reverse=`), then a second `list.sort()` by group only (no
  `reverse=`). Combining both into a single `(group, value)` tuple with
  `reverse=True` would flip the group order too on a descending sort (files
  before folders) - relying on Python's sort being stable is what keeps the
  group order fixed while the value order still reverses.

### Migrations (`app/db/migrations/*.sql`)

Same mechanism as `my-redis-viewer`: add a new numbered `.sql` file (next
sequence number), never edit an already-applied one. `run_migrations()` applies
any file not yet recorded in `schema_migrations`, in filename order, once per
file. `app/db/paths.py` resolves the migrations directory via `sys._MEIPASS`
when frozen so it works both from source and from a PyInstaller build - any new
migration file is automatically picked up by `myfileviewer.spec`'s
`datas=[('app/db/migrations', 'app/db/migrations')]` glob-style entry, no spec
change needed unless the directory itself moves.

### PyInstaller packaging gotchas (see my-redis-viewer's/my-data-viewer's git history for the original incident)

- **Always build with this project's own venv's pyinstaller**
  (`.venv/bin/pyinstaller`), never a bare `pyinstaller` resolved from `PATH`.
- **Build via `pyinstaller --noconfirm myfileviewer.spec`**, not
  `pyinstaller ... main.py` - the latter regenerates/overwrites
  `myfileviewer.spec` from scratch, silently wiping the `datas` entry that
  bundles the SQL migrations.
- **The runnable output is `dist/myfileviewer/myfileviewer`.** `build/` is
  PyInstaller's intermediate scratch directory, never a complete runnable tree.

## Verification performed

No committed test suite yet. The full flow (startup restoring the last folder,
opening a folder, sorting each column both directions and confirming
folders-still-sort-first, navigating into/out of a subfolder, adding/removing a
favorite from both the toolbar button and the sidebar's context menu, sidebar
collapse, and settings persistence) was scratch-verified by driving the real
`MainFrame`/`FolderExplorerPage`/`FavoritesSidebar` objects' actual event
handlers in-process against a real `wx.App` (never shown, no mouse automation),
pumping `wx.YieldIfNeeded` so the background thread's `wx.CallAfter` callbacks
actually ran - the same approach `my-disk-viewer`'s own CLAUDE.md documents. This
caught a real bug before it shipped: combining the folders-first group key and
the sort value into one `reverse=True` sort flipped the group order on a
descending click - fixed by splitting it into two stable sorts (see above).

## What's next (not built yet)

- Recursive folder size (a directory's Size column currently always reads "-").
- A file-type column and complex glob-based selection (mentioned in the initial
  spec as planned future columns/features).
- More file details (a details pane/dialog for a selected entry).
- Packaging beyond the PyInstaller `.spec`/GitHub Actions build - no AUR
  `PKGBUILD` and no `docs/my-file-viewer/` GitHub Pages homepage yet, unlike the
  sibling apps (neither was part of the initial spec for this project).
