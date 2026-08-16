# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app: a performant local file explorer. Pin folders as
**favorites** in a collapsible left sidebar, open a folder to see its contents
in a sortable tree (Name, Size, Modified) - expand a subfolder row to reveal
*its* contents in place, queried lazily only at the moment it's expanded - and
pick up where you left off - the last folder open is remembered in
preferences, same as whether the sidebar was collapsed. File > Settings...
opens a modal for app preferences - today just whether hidden files/folders
are shown (off by default).

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
  listing landing after the user already navigated elsewhere. The per-row
  equivalent for a lazily-expanded subfolder is `FolderTreeCtrl._Node` itself
  (see below): `_on_children_loaded` checks `node.wx_item is not None` rather
  than a path/id, since the node object - not a path string - is what a
  slow expand's callback closure actually captures.

- **Per-expand throwaway `AsyncTaskRunner`, not the shared one**: expanding a
  row goes through `FileSystemService.list_folder` too, but via a fresh
  `AsyncTaskRunner(self)` created on the spot in
  `FolderExplorerPage._on_expand_folder`, not the page's shared `self._async`.
  Several rows can be expanded before the first fetch lands, and `self._async`
  (used for top-level folder navigation) only runs one job at a time - reusing
  it here would silently drop all but the first concurrent expand, leaving
  those rows stuck on "Loading…" forever.

### Folder contents tree (`app/folder_tree_ctrl.py`)

`FolderTreeCtrl` is a `wx.dataview.TreeListCtrl` - top-level items are the
currently open folder's immediate children, same as before, but a subfolder
row can be expanded in place to reveal *its* immediate children instead of
navigating away from it. Three columns today (Name, Size, Modified); more
(file type, glob match, recursive size, ...) are meant to be added to
`_COLUMNS`/`_SORT_KEYS` alongside `FileEntry` gaining the backing field.
Clicking a header sorts by it, click again to reverse.

- **A folder's contents are only ever queried once it's expanded** - the
  whole point of this control. Expanding a row shows a single "Loading…"
  placeholder immediately (native `TL_...`/`EVT_TREELIST_ITEM_EXPANDING`
  behavior lets the arrow flip and the row open before the real data
  arrives), which `FolderTreeCtrl._on_children_loaded` swaps for the real
  children once `FolderExplorerPage`'s async fetch completes - each of
  *those* children that's itself a folder gets its own not-yet-loaded
  placeholder, so depth is unbounded but nothing below the currently-expanded
  rows is ever fetched. A `_Node`'s `children`/`loaded` act as a permanent
  cache: re-expanding an already-loaded folder, or re-sorting, never
  re-queries `FileSystemService` - see `_Node`'s docstring.

- **Sorting is fully hand-rolled, not `wx.dataview`'s native comparator-driven
  column sort** - a header click flips `_sort_column`/`_sort_ascending` (same
  state machine as the old flat-list control) and every currently-loaded
  level is rebuilt from the cached `_Node` tree. Two things pushed this away
  from the native mechanism, both found by hand-testing against a real
  `wx.App` (see "Verification performed"): the native sort's comparator gets
  called and `EVT_TREELIST_COLUMN_SORTED` does fire, but it does **not**
  reorder what `GetFirstChild`/`GetNextSibling` actually traverse, so relying
  on it would desync our own bookkeeping from the screen; and calling
  `TreeListCtrl.SetSortColumn()` - the obvious way to get the native sort-arrow
  header glyph - silently collapses every expanded row as a side effect, in
  this wx version. The header still shows a sort arrow, but drawn by hand
  (`_update_column_headers` appends "↑"/"↓" to the label via
  `ClearColumns()`/`AppendColumn()`), the same trick the old
  `FolderContentsCtrl` used for its plain `wx.ListCtrl`.

- **Folders always sort before files**, regardless of the active column or
  direction - the usual file-explorer convention. This is implemented as **two
  stable sorts**, not one sort keyed on `(group, value)`: first by the column's
  key (honoring `reverse=`), then a second `list.sort()` by group only (no
  `reverse=`). Combining both into a single `(group, value)` tuple with
  `reverse=True` would flip the group order too on a descending sort (files
  before folders) - relying on Python's sort being stable is what keeps the
  group order fixed while the value order still reverses. `FolderTreeCtrl`
  applies this per level (`_sorted`), not just at the top.

- **Keyboard**: Space toggles the selected row's expand/collapse state
  (`_toggle_selected_expand`), reusing `_on_item_expanding`'s lazy-load path
  rather than duplicating it - `TreeListCtrl.Expand()` fires
  `EVT_TREELIST_ITEM_EXPANDING` the same as an arrow click does, confirmed by
  hand-testing, so Space never needs its own fetch logic. Enter/double-click
  activation needed no new code at all: `wx.dataview.TreeListCtrl` already
  fires `EVT_TREELIST_ITEM_ACTIVATED` on the Enter key natively.

- **"Collapse all" button** (`FolderExplorerPage._collapse_all_btn`, next to
  the breadcrumb) calls `FolderTreeCtrl.collapse_all()`, which walks every
  loaded `_Node` and calls `Collapse()` on it - a UI-only operation, so the
  `_Node.children` cache is untouched and re-expanding afterwards still won't
  re-query `FileSystemService`. The button lives in its own row sizer
  alongside (not inside) `_breadcrumb_panel`, since `_rebuild_breadcrumb`
  calls `Clear(delete_windows=True)` on `_breadcrumb_sizer` on every
  navigation and would otherwise destroy it.

- **Expand/collapse state survives a re-sort** even though there's no
  `EVT_TREELIST_ITEM_COLLAPSED` to track it incrementally: right before
  `_rebuild_all` tears the wx tree down, `_snapshot_expanded` asks
  `IsExpanded()` on every currently-built node and stores the answer on the
  `_Node` itself, then the rebuild re-`Expand()`s whichever nodes came back
  `True`.

### Settings (`app/settings_dialog.py`, File > Settings...)

`SettingsDialog` is a plain `wx.Dialog` with a `CreateButtonSizer(OK|CANCEL)`,
the same shape as `my-redis-viewer`'s `ProfileDialog` - today it holds one
control (a checkbox for "Show hidden files and folders"), but a future setting
is just another control in `_build_ui` plus a field on the OK-captured result,
not a new pattern. `MainFrame._on_settings` only writes through to
`SettingsRepository`/`FolderExplorerPage` when `ShowModal()` returns
`wx.ID_OK` - Cancel discards whatever the user ticked, same as any other
modal form in this app family.

- **The setting itself lives in `SettingsRepository`**
  (`get_show_hidden_files`/`set_show_hidden_files`, key `show_hidden_files`) -
  no new migration needed, since `settings` is already a generic key/value
  table. Defaults to `False` (hidden files/folders not shown) the same way
  `get_sidebar_collapsed` defaults to `False`: no row yet reads back as the
  falsy string comparison failing.

- **`FileSystemService.list_folder` takes `show_hidden: bool = False`** and
  filters out any entry `_is_hidden()` flags (a leading `.` - the Unix
  convention - or, on Windows, `stat_result.st_file_attributes &
  FILE_ATTRIBUTE_HIDDEN`) before it's even wrapped into a `FileEntry`. A
  filtered-out entry is *not* counted in `FolderListing.skipped`: that field
  means "couldn't be read", and hiding a dotfile on purpose isn't a failure.
  `stat.FILE_ATTRIBUTE_HIDDEN` is checked unconditionally rather than gated on
  `sys.platform`, since the constant exists cross-platform in Python's `stat`
  module even though `st_file_attributes` only actually appears on Windows
  stat results (`getattr(..., 0)` covers everywhere else).

- **`FolderExplorerPage` is the one place that knows the current preference**
  (`self._show_hidden`, seeded from `MainFrame`'s constructor call via
  `show_hidden=settings_repository.get_show_hidden_files()`) and passes it on
  *every* `list_folder` call - both `_load_current_folder`'s top-level load
  and `_on_expand_folder`'s per-row lazy fetch. `set_show_hidden` (called by
  `MainFrame._on_settings` after a successful OK) reloads the currently open
  folder so the new preference takes effect immediately; it's a no-op if the
  value didn't actually change. Reloading resets the tree to its unloaded/
  collapsed state the same as any other top-level reload (`open_folder`,
  `_on_up`, ...) - an acceptable trade-off since changing this setting is a
  deliberate, infrequent action, not a hot path worth preserving expand state
  across.

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

`FolderTreeCtrl`'s lazy-expand rework was verified the same way, driving its
real `_on_item_expanding`/`_on_children_loaded`/`_on_column_sorted` handlers
with hand-constructed fake events (duck-typed objects exposing just
`GetItem()`/`GetColumn()`, matching what a real `wx.dataview.TreeListEvent`
exposes) against a real multi-level temp-folder tree - confirming a folder's
`FileSystemService.list_folder` call happens exactly once, only on its first
expand, that a nested grandchild folder stays unqueried until *it's* expanded,
and that re-expanding or re-sorting never triggers a second query. This is
also what caught both of `SetSortColumn()`'s and native-comparator-sort's
issues documented above - real `EVT_TREELIST_COLUMN_SORTED` header clicks
(checked separately with `wx.UIActionSimulator`, since that particular
question was about a real click's side effects, not about our own handler)
confirmed the collapse-on-sort bug traced back to our own `SetSortColumn()`
call rather than the click itself.

Space-to-expand/collapse and the "Collapse all" button were verified both the
fake-event way (calling `_on_key_down`/`_toggle_selected_expand`/
`collapse_all` directly) and with real input against a shown-but-untouched-by-
mouse `wx.App` window via `wx.UIActionSimulator` - a real Space keypress with
a folder row selected, and a real click on the toolbar button - confirming
both actually reach the same lazy-load path a real arrow click does, and that
collapsing (either way) leaves the `_Node` cache intact rather than forcing a
re-query on the next expand. Enter/double-click activation needed no
verification beyond confirming it was already native - see
`FolderTreeCtrl`'s docstring.

The hidden-files setting was verified end to end against a real temp folder
tree containing both dotfiles and a dotfile-named subfolder: confirmed
`FileSystemService.list_folder` excludes them by default and includes them
with `show_hidden=True`, that `SettingsRepository.get_show_hidden_files`
defaults to `False` and round-trips through `set_show_hidden_files`, that
`FolderExplorerPage.set_show_hidden` reloads the open folder and that
expanding a now-visible hidden folder fetches *its* contents correctly too,
and that the setting is a genuine no-op (`_load_current_folder` doesn't fire
again) when set to its current value. `SettingsDialog` was driven the same
"never shown" way as everything else in this file for its checkbox state,
but its OK/Cancel result plumbing specifically needed a real `ShowModal()`
loop (`wx.CallAfter` to click the button mid-modal) - `EndModal()` asserts if
called without an active `ShowModal()`, so calling `_on_ok` directly the way
other handlers in this file are tested doesn't work for dialogs.

## What's next (not built yet)

- Recursive folder size (a directory's Size column currently always reads "-").
- A file-type column and complex glob-based selection (mentioned in the initial
  spec as planned future columns/features).
- More file details (a details pane/dialog for a selected entry).
- Packaging beyond the PyInstaller `.spec`/GitHub Actions build - no AUR
  `PKGBUILD` and no `docs/my-file-viewer/` GitHub Pages homepage yet, unlike the
  sibling apps (neither was part of the initial spec for this project).
