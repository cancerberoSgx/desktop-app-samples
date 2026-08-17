# My File Viewer

A desktop app, built with [wxPython](https://wxpython.org), for browsing folders on
your local filesystem: pin folders as favorites in a collapsible sidebar, open a
folder and see its contents in a sortable tree (name, size, modified date) -
expand a subfolder row to see its contents in place, queried only at the
moment it's expanded - and pick up right where you left off - the last folder
you had open is remembered across restarts.

## Project layout

```
main.py                       Entry point
app/
  frame.py                    Main window: sidebar + folder explorer
  sidebar.py                  Collapsible left sidebar: favorite-folder shortcuts
  folder_explorer_page.py     Toolbar + breadcrumb + folder contents tree
  folder_tree_ctrl.py         Sortable wx.dataview.TreeListCtrl, lazy per-row expand
  settings_dialog.py           File > Settings... modal (show hidden files, ...)
  file_system_service.py      Every filesystem action - see "Async by design" below
  async_task.py                Facade for running a service call off the UI thread
  formatting.py                Byte-count / timestamp -> human string helpers
  models.py                    Favorite / FileEntry / FolderListing dataclasses
  repositories.py              FavoriteRepository / SettingsRepository (SQLite)
  db/
    paths.py                  Resolves ~/.my-file-viewer and the migrations folder
    connection.py              SQLite connection factory
    migrator.py                 Applies any new *.sql file under db/migrations/
    migrations/
      0001_create_favorites.sql
      0002_create_settings.sql
requirements.txt
myfileviewer.spec
```

## Data storage

App data (favorites and preferences) lives in a SQLite database at
`~/.my-file-viewer/my-file-viewer.db`, created on first run. Schema changes are
made by adding a new numbered `.sql` file under `app/db/migrations/` (e.g.
`0003_add_something.sql`) - `run_migrations()` applies any file not yet recorded
in the `schema_migrations` table, in filename order.

## Favorites

Pin any folder as a favorite from the "☆ Add to Favorites" button in the toolbar
(disabled once the open folder is already a favorite). Favorites show up as a
list in the left sidebar - click one to jump straight to that folder. Right-click
a favorite to remove it. The sidebar is collapsible (the arrow button at its top)
to an icon-only strip, and whether it's collapsed is remembered in preferences.

## Folder explorer

"Open Folder..." (or clicking a favorite, or double-clicking a subfolder row)
opens a folder and lists its immediate contents - files and subfolders - in a
tree with **Name**, **Size**, and **Modified** columns. Click a column header to
sort by it; click again to reverse direction. Folders always sort before files,
regardless of which column is active. The tree is a `wx.dataview.TreeListCtrl`
(`FolderTreeCtrl`); expanding a subfolder row queries *that* folder's contents
lazily, right then, rather than up front - so opening a folder stays fast no
matter how deep or wide the tree underneath it gets, and a folder's contents
are only ever fetched once (re-expanding or re-sorting reuses what was already
fetched). Double-clicking a file (or pressing **Enter**) opens it with the OS's
default application; double-clicking a folder (or pressing **Enter**) navigates
into it, making it the new top-level view. Press **Space** on a selected
folder row to expand or collapse it in place without navigating - the "−"
button next to the breadcrumb collapses every currently-expanded row at once
(without forgetting what's already been fetched, so re-expanding stays
instant). The last folder you had open is remembered and reopened on startup.

One or more rows can be selected at a time. Up/Down/PageUp/PageDown/Home/End
move a single selection; hold **Shift** with any of them to extend a *range*
selection instead; clicking a row, or pressing any of those keys without
Shift, collapses back to a single selection. The status bar's right-hand
field always shows **Selected: N**.

Directories don't show a size yet (`-`) - recursive folder size is a planned
future column, not computed today.

## Settings

**File > Settings...** opens a modal with the app's preferences - today just
**Show hidden files and folders** (off by default, matching most file
explorers). Ticking it and clicking OK reloads whatever folder is currently
open so it takes effect right away; Cancel discards the change. The
preference is remembered across restarts the same way the last-opened folder
and sidebar collapse state are.

## Async by design

Every filesystem action lives in `FileSystemService` (`app/file_system_service.py`)
and is invoked through `AsyncTaskRunner` (`app/async_task.py`), never called
directly from a `wx.EVT_*` handler - see `FolderExplorerPage._load_current_folder`
for the reference usage. This is the same pattern `my-redis-viewer` uses to keep
Redis commands from freezing the UI, carried over here for filesystem calls
(which can stall just as badly on a network-mounted folder). Expanding a
subfolder row in the tree goes through the same `FileSystemService.list_folder`
call, but via its own throwaway `AsyncTaskRunner` (see
`FolderExplorerPage._on_expand_folder`) rather than the page's shared one, so
several rows can be expanded - and queried - concurrently. Every future folder
action - rename, delete, copy/move, recursive size, glob-based search, more
columns/file details - should be added as a new `FileSystemService` method and
called the same way.

## What's not here (yet)

Per the initial spec, this project intentionally has **no profiles and no
datasources** (unlike `my-redis-viewer`/`my-data-viewer`) - it's a single local
file explorer, not a multi-connection tool. Planned future columns/features:
file type, complex glob-based selection, recursive folder size, more file
details.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python3 main.py
```

## Building standalone executables

Standalone executables are built with [PyInstaller](https://pyinstaller.org).
PyInstaller does not cross-compile, so each executable must be built on its
target OS (build the Linux binary on Linux, the Windows `.exe` on Windows,
and the macOS `.app` on macOS).

```bash
.venv/bin/pip install pyinstaller     # once, into THIS project's venv
.venv/bin/pyinstaller --noconfirm myfileviewer.spec
./dist/myfileviewer/myfileviewer      # the runnable output
```

A GitHub Actions workflow (`.github/workflows/my-file-viewer-build.yml`) builds
all three platforms and publishes them as release assets - see that file's
comments for the versioned-vs-"latest" release convention.
