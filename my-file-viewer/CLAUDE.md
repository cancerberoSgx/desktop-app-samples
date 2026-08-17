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
are shown (off by default). One or more rows can be selected at a time
(Shift+Up/Down/PageUp/PageDown/Home/End for a range, any of those without
Shift for a single row) - the status bar's right-hand field always reads
"Selected: N".

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
python3 main.py <path>   # optional: open a folder, or a file (selected/scrolled
                          # into view) in its parent folder - relative or absolute

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
or on first run) - unless `main.py` was given a command-line path (see below),
which takes priority over restoring the last folder.

- **Command-line target**: `main.py` passes `sys.argv[1]` (if given) through
  as `MainFrame(initial_path=...)` - `myfileviewer <path>`, relative or
  absolute (relative resolves against the process's cwd at startup, same as
  any other CLI tool). `_restore_last_folder` tries
  `FolderExplorerPage.open_path(self._initial_path)` first when one was
  given; `open_path` is the one place that knows "a folder opens directly,
  a file opens via its *parent* folder with the file selected and scrolled
  into view" (`open_folder(..., select_path=...)`, same mechanism
  `try_paste_navigate`'s Enter handler already uses for a pasted file path -
  both funnel through `open_path` rather than duplicating the
  is-it-a-file-or-a-folder check). A CLI path that doesn't resolve to
  anything real (typo, since-deleted target) falls through to the normal
  last-folder-or-home fallback silently, no error popup - the same
  "don't be more disruptive than a stale remembered folder already is"
  convention `_restore_last_folder` already followed before this existed.

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

- **Multi-selection (`TL_MULTIPLE`)** - Up/Down/PageUp/PageDown/Home/End move
  a single selection; holding Shift with any of them extends a *range*
  selection instead; a plain click or keypress without Shift collapses back
  to a single selection. All of this is native GTK `TreeView` behavior that
  comes for free once `TL_MULTIPLE` is set in the constructor's `style=` -
  no custom key handling needed, confirmed by hand-testing with
  `wx.UIActionSimulator` (see "Verification performed"). The one thing
  `TL_MULTIPLE` requires everywhere: **never call `GetSelection()`
  (singular)** - it hard-asserts once `TL_MULTIPLE` is set - always
  `GetSelections()` (plural, `FolderTreeCtrl.get_selected_entries()`), even
  for "is exactly one thing selected" checks. `_toggle_selected_expand`
  (Space) and `_rebuild_all`'s selection-preserving `_reselect` were both
  updated to iterate every selected node, not just one, so a Space-expand or
  a re-sort acts on (or preserves) a whole multi-selection rather than
  silently collapsing it to a single row.

- **A hard "last Bind() wins" gotcha, specific to this wx build**: binding a
  *second* handler for the same `(event type, window)` pair - even on an
  unrelated widget like a plain `wx.Button`'s `EVT_BUTTON` - silently
  replaces the first handler instead of adding to it, confirmed by
  hand-testing (multiple independent repros, including two `Bind()` calls
  for `EVT_TREELIST_SELECTION_CHANGED` on the same `FolderTreeCtrl`, only the
  most-recently-bound one ever fires). This bit "Selected: N" for real: the
  fix was `FolderTreeCtrl` binding `EVT_TREELIST_SELECTION_CHANGED` to its
  own internal handler exactly once, in its own `__init__`, and
  `FolderExplorerPage` reacting to selection changes only through the
  `on_selection_changed` *callback* parameter (an ordinary function call,
  not a second `Bind()`) - see `FolderExplorerPage._on_tree_selection_changed`,
  which is what actually updates button states *and* forwards the count to
  `MainFrame`. **Never add a second `.Bind()` call for an event type another
  piece of code already binds on the same object** - route through a
  callback parameter instead, the way `on_activate_entry`/`on_expand_folder`/
  `on_selection_changed` already do.

- **"Selected: N" status bar field** - `MainFrame.CreateStatusBar(2)`, with
  `SetStatusWidths([-1, 120])` so field 0 (the existing "Viewing: .../Ready"
  text) stretches and field 1 stays a fixed-width right-hand column.
  `FolderTreeCtrl`'s `on_selection_changed(count)` callback flows
  `FolderTreeCtrl` -> `FolderExplorerPage._on_tree_selection_changed` ->
  `MainFrame._on_selection_changed`, which is the only place that actually
  calls `SetStatusText(f"Selected: {count}", 1)`.

### Clipboard: copy path button, Edit > Copy Paths, paste-a-path navigation (`FolderExplorerPage`)

The breadcrumb row has one extra control beyond the clickable path segments
themselves - a copy button (`_copy_path_btn`, "⧉") that puts the currently
open folder's path on the clipboard. Two more clipboard actions live on the
`&Edit` menu instead of a button, since neither needs one: `wx.ID_COPY`
("Copy Paths\tCtrl+C") and `wx.ID_PASTE` ("Paste\tCtrl+V") -
`MainFrame._build_menu_bar` gives each its accelerator, so Ctrl+C/Ctrl+V work
from anywhere in the window (not just with a menu open), the same way
`Alt+F4` does for Exit. `_copy_path_btn` sits as a sibling of
`_breadcrumb_panel` in `breadcrumb_row`, same as `_collapse_all_btn`, so
`_rebuild_breadcrumb`'s `Clear(delete_windows=True)` never touches it.

- **Copy Paths (Ctrl+C)**: `MainFrame._on_copy_paths` (bound to `wx.ID_COPY`)
  calls `FolderExplorerPage.copy_selected_paths()`, which joins
  `FolderTreeCtrl.get_selected_entries()`'s `FileEntry.path` (already
  absolute - see `FileSystemService.list_folder`) one per line and puts that
  on the clipboard - a no-op if nothing is selected. Works for any number of
  selected rows, files and folders alike, since `get_selected_entries`
  already handles the whole multi-selection (see "Multi-selection" above),
  not just a single row.

- **Paste-a-path flow**: `MainFrame._on_paste` (bound to `wx.ID_PASTE`) calls
  `FolderExplorerPage.try_paste_navigate()`, the one entry point for this
  whole feature. It reads clipboard text (`_get_clipboard_text`) and checks
  whether it resolves to an existing file or folder (`_resolve_existing_path`
  - trims whitespace/surrounding quotes, expands `~`, then `os.path.exists`);
  anything else on the clipboard is silently ignored, since there's no other
  editable text field in this app for a plain Paste to make sense against.
  A resolving path swaps the breadcrumb for a focused `wx.TextCtrl` prefilled
  with it (`_enter_breadcrumb_edit_mode`) instead of navigating immediately -
  Enter confirms (`_on_breadcrumb_edit_enter`), Escape discards it and
  reverts to the normal breadcrumb without navigating
  (`_on_breadcrumb_edit_key_down`). Typing further edits before Enter also
  works, since it's a real, focused `wx.TextCtrl` - not read-only.

- **A file path selects the file, not just its folder**: on Enter,
  `_navigate_to_pasted_path` calls `open_folder` directly for a folder, or
  for a file, calls `open_folder(os.path.dirname(file_path),
  select_path=file_path)` - a new optional param on `open_folder` (still the
  one entry point every navigation funnels through). `_on_folder_loaded`
  applies `_pending_select_path` via `FolderTreeCtrl.select_path` once the
  listing for that folder actually lands, then clears it - a resolved file is
  always a *root* entry of the folder just opened, never a nested one, so
  `select_path` only ever needs to search `_roots`, no expanding involved.
  `open_folder` always assigns `_pending_select_path` (even `None` on every
  other call site) so a stale selection from an earlier paste can never leak
  into an unrelated later navigation.

- **Returning to the normal breadcrumb needs no separate "revert" step on
  Enter**: `open_folder` already calls `_rebuild_breadcrumb()` on every
  successful navigation, which is exactly what swaps the `wx.TextCtrl` back
  out for the normal clickable segments - `_navigate_to_pasted_path` doesn't
  need to do anything extra for that part. An Enter that *doesn't* resolve to
  a real path (the user edited it into nonsense) shows the same "not a
  folder"-style `wx.MessageBox` the rest of this page uses, then explicitly
  calls `_rebuild_breadcrumb()` itself, since no `open_folder` call happens
  in that path to do it automatically.

### File actions: Open / Rename / Delete / Properties (`FolderExplorerPage`, `FolderTreeCtrl`)

Four actions apply to whatever's selected in the tree - Open, Rename, and
Properties only make sense for exactly one selected row, Delete works on any
non-empty selection (one or many). All four are reachable from the tree's
right-click context menu and the File menu; Open/Rename/Delete additionally
have their own keyboard shortcuts (Enter/double-click, F2, Delete -
Properties doesn't get one, matching most file managers). Every route
funnels through the same four `FolderExplorerPage` methods
(`open_selected`/`rename_selected`/`delete_selected`/
`show_properties_for_selected`), so each action's actual behavior - and its
"is this legal right now" rule - lives in exactly one place.

- **The tree control only ever reports intent, never decides legality**:
  `FolderTreeCtrl`'s Delete/F2 key handling and its `on_context_menu`
  callback all report "the user asked to do X" (or hand back whatever's
  currently selected); whether X is actually allowed for the current
  selection size is decided by the caller - `FolderExplorerPage`'s own
  methods for the keyboard/context-menu paths, `MainFrame._on_selection_changed`
  for the File menu's enabled state. This mirrors the "Selected: N" callback
  pattern already in this file: a single source of truth instead of the
  tree and the frame each independently tracking "is Rename legal right
  now" and risking disagreement.

- **File menu enabled state**: `MainFrame._build_menu_bar` creates
  `_open_menu_item`/`_rename_menu_item`/`_delete_menu_item`/
  `_properties_menu_item` ("Open\tEnter"/"Rename\tF2"/"Delete\tDel"/
  "Properties..."), all disabled until a selection exists.
  `_on_selection_changed` (already the callback wired from
  `FolderTreeCtrl` -> `FolderExplorerPage._on_tree_selection_changed` for
  "Selected: N") is the one place that also flips these four based on
  `count` - `count == 1` for Open/Rename/Properties, `count >= 1` for
  Delete. The context menu (`FolderExplorerPage._on_tree_context_menu`)
  applies the identical rule to its own "Open"/"Rename"/"Properties" items -
  laid out as Open/Rename, a separator, Delete, a separator, Properties
  (Properties last, its own group, is the usual file-manager convention).

- **Why Enter and the "Open\tEnter" menu accelerator don't double-fire**:
  Enter/double-click activation is unchanged, native
  `EVT_TREELIST_ITEM_ACTIVATED` behavior (see the tree's own docstring) -
  `open_selected` (called from the File menu) just reuses the same
  `_on_activate_entry` that activation already calls, so "Open" behaves
  identically everywhere it can be triggered from. Confirmed by hand-testing
  with `wx.UIActionSimulator` that a real Enter keypress with a row selected
  only ever triggers the native activation path, not the menu's - see
  "Verification performed".

- **Why Delete/F2 don't double-fire against the same-named menu
  accelerators**: `FolderTreeCtrl._on_key_down` handles `WXK_DELETE`/`WXK_F2`
  itself and deliberately does *not* call `event.Skip()` for them (same as
  it already didn't for Space) - an unskipped `EVT_KEY_DOWN` is consumed
  locally and never reaches the frame's menu accelerator table, so despite
  the File menu items carrying the same "\tDel"/"\tF2" accelerator text
  (there purely for on-screen discoverability), only the tree's own handler
  ever actually fires for either key while it has focus. Confirmed by
  hand-testing with `wx.UIActionSimulator` - see "Verification performed".

- **Right-click preserves an existing multi-selection**
  (`FolderTreeCtrl._on_item_context_menu`, bound to
  `EVT_TREELIST_ITEM_CONTEXT_MENU`): right-clicking a row that's already
  part of the current selection leaves the whole selection alone before
  calling `on_context_menu`, so "select three rows, right-click one of
  them" doesn't collapse it down to one, the way a naive
  `UnselectAll()`-then-`Select()` would. Right-clicking a row *outside* the
  current selection replaces it with just that row first - the same
  convention most file managers use. `IsSelected(item)` (not membership-
  testing `GetSelections()`, whose `TreeListItem` results don't support
  `==`/`in` reliably) is what makes this check possible.

- **Rename and Delete never do a full `_rebuild_all()`** - both mutate the
  wx tree surgically instead (`FolderTreeCtrl.apply_rename`/`remove_paths`):
  `apply_rename` calls `SetItemText` on just the renamed row's existing wx
  item, and `remove_paths` calls `DeleteItem` on just the removed row(s)'
  wx items. This is deliberate, not an oversight: `_rebuild_all()` tears
  down every row via `DeleteAllItems()` and re-adds them all, which resets
  the control's vertical scroll position back to the top - jarring if the
  user just deleted or renamed one row out of a long, scrolled-down
  listing. Confirmed by hand-testing (see "Verification performed") that
  neither method ever calls `DeleteAllItems()`, and that unrelated rows'
  underlying wx items survive both operations with the same identity
  (proof no rebuild happened, not just "looks the same").

  - The trade-off: a renamed row doesn't jump to its new alphabetically-sorted
    position right away (it'll land there on the next re-sort or reload)
    the way a full rebuild's `_sorted()` pass would have put it - a much
    smaller cost than losing the user's scroll position on every rename.
  - If the renamed entry is a folder that had already been expanded/loaded,
    `apply_rename` still can't patch its cached children in place (renaming
    a folder changes every descendant's real path too), so it deletes their
    wx rows, drops the cache, and appends a single "Loading…" placeholder -
    the same never-expanded-yet state a folder starts in, so a future
    expand re-queries `FileSystemService` correctly. This is the same
    reset-rather-than-patch trade-off `set_show_hidden` already makes.
  - `remove_paths` also drops each removed node from wherever it lives in
    the cached `_Node` tree (top-level `_roots` or an already-loaded
    parent's `children`) so it can't resurface on some later resort -
    `DeleteItem` on a folder's wx item natively removes its descendant rows
    too, so a removed folder's own cached children don't need separate
    cleanup.

- **`FolderExplorerPage.delete_selected` drops redundant descendants before
  calling `FileSystemService.delete`**: if both a folder and something
  inside it are selected together, deleting the folder already removes the
  descendant, so a separate delete call for it would just fail (it's gone
  by the time its own turn comes) for no reason. `_filter_top_level_selected`
  (sorts shortest-path-first, then keeps a path only if it isn't already
  nested under a path already kept) is what prunes those before the batch
  ever reaches `FileSystemService.delete`.

- **`FileSystemService.delete`/`rename` are batch-tolerant, not
  fail-fast**: `delete(paths)` returns a `DeleteResult` (`deleted`,
  `errors` - path to message) with each path succeeding or failing
  independently, the same "don't abort the whole call over one bad entry"
  spirit as `list_folder`'s `skipped` count - a read-only file in an
  otherwise-deletable multi-selection shouldn't block the rest. `rename`
  raises (`ValueError` for an empty name or one containing a path
  separator, otherwise whatever `os.rename` itself raises) since it's
  always exactly one entry - both exception types are surfaced identically
  by `AsyncTaskRunner`'s `on_error`, no special-casing needed at the call
  site.

- **Delete confirmation is a Settings checkbox** ("Ask for confirmation
  before deleting", `SettingsRepository.get_confirm_delete`/
  `set_confirm_delete`, key `confirm_delete`) - see the Settings section
  below for why it defaults to `True`, the opposite of this app's other
  settings. `delete_selected` only shows the confirmation `wx.MessageBox`
  (wording depends on whether it's one item, named, or several, via
  `_confirm_delete_message`) when `self._confirm_delete` is true; Cancel
  (`wx.NO`) aborts before `FileSystemService.delete` is ever called, the
  same "only act on an explicit OK/Yes" convention `SettingsDialog`/
  `open_folder`'s error `wx.MessageBox` already follow.

### Properties dialog (`app/properties_dialog.py`, File > Properties... / right-click > Properties)

`PropertiesDialog` is a plain `wx.Dialog` with a `CreateButtonSizer(OK)` (no
Cancel - it's read-only, there's nothing to submit), same dialog family as
`SettingsDialog`. `FolderExplorerPage.show_properties_for_selected` (only
meaningful for exactly one selected row, same as Open/Rename) constructs and
`ShowModal()`s it for that entry's path, then `Destroy()`s it - the same
try/finally shape `MainFrame._on_settings` already uses for `SettingsDialog`.

- **Two separate `FileSystemService` methods, not one** -
  `get_properties(path)` (name, extension, full path, permissions
  (`stat.filemode(stat.st_mode)`), and created/modified/accessed dates -
  one `os.stat()` call, always fast) and `calculate_folder_size(path)`
  (walks the whole tree with `os.walk`/`os.lstat`, potentially slow for a
  big folder). Both are still only ever called through `AsyncTaskRunner`
  per this app's blanket rule, but splitting them lets the dialog show the
  fast data immediately while the slow one is still in flight, rather than
  the whole dialog waiting on whichever field is most expensive to compute.
  `get_properties` deliberately leaves a folder's *recursive* size out of
  `FileProperties.size_bytes` (that field holds the folder's own,
  meaningless-to-show directory-entry size in that case) - `PropertiesDialog`
  is the one place that knows to read `size_bytes` only when `is_dir` is
  `False`, and otherwise kick off `calculate_folder_size` separately.

- **"Calculating..." while the recursive size is in flight, without
  freezing the UI**: `_on_properties_loaded` (the fast `get_properties`
  fetch's `on_success`) immediately fills in every other field, and for a
  folder, sets the Size field to "Calculating..." and calls
  `_load_folder_size`, which runs `calculate_folder_size` through its own
  throwaway `AsyncTaskRunner` (same "one throwaway runner per independent
  concurrent fetch" convention as `FolderExplorerPage._on_expand_folder`) -
  `_on_folder_size_loaded` replaces the label once it lands. Confirmed by
  hand-testing (see "Verification performed") that `wx.CallAfter`-delivered
  results still arrive correctly even though the dialog is open via a
  blocking `ShowModal()` - the nested modal event loop still pumps the same
  queued events - and, using an artificially slowed
  `calculate_folder_size`, that a `wx.CallLater` timer kept firing on
  schedule throughout the whole wait, proving the main thread was never
  blocked by the calculation.

- **`calculate_folder_size` uses `os.lstat`, not `os.stat`** - a symlink is
  sized as itself rather than followed, avoiding double-counting (or an
  infinite loop on a symlink cycle) a target that's also reachable via a
  real path elsewhere in the same tree. Like `list_folder`'s `skipped`
  count, a file that can't be stat-ed mid-walk (permission denied, removed
  concurrently) is silently skipped rather than aborting the whole
  calculation - Properties only needs one approximate total, not a report
  of what couldn't be read.

- **Creation date is best-effort, platform-dependent**
  (`file_system_service._created_at`): macOS/BSD's real `st_birthtime` is
  used when present; on Windows, `st_ctime` *is* the creation time; on
  Linux, `st_ctime` means metadata-change time, not creation, and the
  stdlib `stat` interface exposes no reliable creation time there at all -
  rather than mislabel metadata-change time as "Created", this case returns
  `None`, which `formatting.format_timestamp` renders as "-". This is also
  why `format_modified` was renamed `format_timestamp`: it's now shared by
  four different timestamp kinds (a tree row's Modified column, plus
  Properties' Created/Modified/Accessed), not just one.

- **The full-path row has its own copy button** (`⧉`, same glyph and
  clipboard mechanism as the breadcrumb's `_copy_path_btn` - see the
  Clipboard section above) - `PropertiesDialog._on_copy_path` puts the
  dialog's `path` argument on the clipboard, independent of
  `FolderExplorerPage.copy_selected_paths`/`_on_copy_path`, since this
  dialog has no access to (and no need for) the tree's current selection.

### Settings (`app/settings_dialog.py`, File > Settings...)

`SettingsDialog` is a plain `wx.Dialog` with a `CreateButtonSizer(OK|CANCEL)`,
the same shape as `my-redis-viewer`'s `ProfileDialog` - today it holds two
checkboxes ("Show hidden files and folders", "Ask for confirmation before
deleting"), but a future setting is just another control in `_build_ui` plus
a field on the OK-captured result, not a new pattern. `MainFrame._on_settings`
only writes through to `SettingsRepository`/`FolderExplorerPage` when
`ShowModal()` returns `wx.ID_OK` - Cancel discards whatever the user ticked,
same as any other modal form in this app family.

- **`get_confirm_delete` defaults to `True`** (ask before deleting) when
  never set - the opposite convention from `get_show_hidden_files`/
  `get_sidebar_collapsed`, both of which default `False` on no row. It's
  implemented as `self.get(CONFIRM_DELETE_SETTING_KEY) != "0"` rather than
  the usual `== "1"`, which is what makes "no row yet" read as `True`
  instead of `False` - asking before an irreversible action is the safer
  out-of-the-box behavior. `FolderExplorerPage.set_confirm_delete` just
  updates the flag read on the next delete; unlike `set_show_hidden`, it
  doesn't reload anything, since this setting doesn't change what's
  displayed.

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

Multi-selection was verified with real input (`wx.UIActionSimulator`) against
a real, shown `wx.App` window - a real Shift+Down twice extended a 1-row
selection to 3, a plain Down afterwards collapsed it back to 1, and
Shift+End/plain Home behaved the same way - confirming Up/Down/PageUp/
PageDown/Home/End with/without Shift all work natively once `TL_MULTIPLE` is
set, no extra code needed. This is also how the "last `Bind()` wins" gotcha
above was found and confirmed: three independent repros (plain lambdas bound
directly to a bare `TreeListCtrl`'s `EVT_TREELIST_SELECTION_CHANGED`, and
even two `wx.Button` `EVT_BUTTON` handlers on the same button) all showed
only the most-recently-bound handler ever firing on a real, subsequent
event - which is what had silently broken `FolderExplorerPage`'s original
`self._list.Bind(EVT_TREELIST_SELECTION_CHANGED, self._update_button_states)`
line: it was clobbering `FolderTreeCtrl`'s own internal binding for the same
event, so the "Selected: N" callback fired exactly once (from
`_rebuild_all`'s direct, non-event call) and never again on a real selection
change. Fixed by removing that second `Bind()` and routing through the
`on_selection_changed` callback parameter instead (see above) - re-verified
with the same real-input test afterwards, confirming both the status bar
text and the button-enabled states it used to drive still update correctly.
Multi-selection surviving a re-sort (`_reselect` preserving every selected
path, not just one) and Space toggling every selected folder independently
were verified the fake-event way, selecting two non-empty folders plus a
file, resorting, and confirming all three stayed selected and both folders
expanded (and fetched their own contents) on a single Space press.

The breadcrumb copy button and paste-a-path navigation were verified against
a real `wx.App` and real temp folders/files, driving `FolderExplorerPage`'s
actual methods (`_on_copy_path`, `try_paste_navigate`,
`_on_breadcrumb_edit_enter`, `_on_breadcrumb_edit_key_down`) with a
hand-constructed `wx.CommandEvent`/`wx.KeyEvent` the same way
`FolderTreeCtrl`'s fake-event tests work: confirmed the copy button round-trips
the open folder's exact path through the real system clipboard; pasting a
file's path swaps the breadcrumb for a focused, prefilled `wx.TextCtrl`
rather than navigating immediately; pressing Enter in it opens the file's
*parent* folder and selects the file itself once the (real, async) listing
lands; pasting a folder's path instead navigates straight there on Enter;
clipboard text that isn't an existing path is silently ignored (breadcrumb
never leaves its normal state); and Escape discards the edit and reverts to
the normal clickable breadcrumb without navigating anywhere. Also confirmed
`open_folder`'s new `select_path` param defaults to clearing
`_pending_select_path` on every other navigation path (Up, a favorite click,
double-clicking a subfolder, ...), so a stale pending selection from an
earlier paste can never resurface on an unrelated later navigation.

Edit > Copy Paths (Ctrl+C) was verified the same way: selecting a mix of
files and a folder in a real temp folder, calling
`FolderExplorerPage.copy_selected_paths()` directly, and confirming the real
clipboard ends up with exactly those rows' absolute paths, one per line,
matching the selection with no extra/missing entries; and that calling it
with nothing selected leaves a pre-existing clipboard value untouched rather
than clobbering it with an empty string.

Open/Rename/Delete were verified against a real `wx.App` and real temp
folders/files, driving both the underlying `FolderExplorerPage` methods
directly and, separately, real keyboard input via `wx.UIActionSimulator`:
renaming a selected file through `rename_selected` (with `wx.TextEntryDialog`
substituted for a fake returning a fixed new name, since a real modal loop
needs the `wx.CallAfter`-driven `ShowModal()` pattern documented above, not a
direct call) actually renamed it on disk and updated the tree row; deleting a
multi-selection of a file and a folder together actually removed both from
disk and from the tree, with `_filter_top_level_selected` confirmed (as a
plain unit test, no wx needed) to drop a path already nested under another
selected path; the confirmation `wx.MessageBox` was confirmed to block the
delete on "No" (file still on disk, still in the tree) and proceed on "Yes",
with `confirm_delete=False` skipping it entirely. Real `wx.UIActionSimulator`
input (not just direct method calls) confirmed a Delete keypress and an F2
keypress each call their respective `FolderExplorerPage` method **exactly
once** despite the File menu carrying the same "\tDel"/"\tF2" accelerator
text - proving `FolderTreeCtrl._on_key_down`'s unskipped `EVT_KEY_DOWN` truly
consumes the keystroke before it reaches the frame's menu accelerator table,
not just "happens to work in this one test." The right-click context menu
was verified with hand-constructed `TreeListEvent`-like objects (same
approach as the tree's other fake-event tests): right-clicking a row already
part of a multi-selection left the whole selection intact, while
right-clicking outside it collapsed the selection to just that row, both
confirmed by inspecting what `on_context_menu` actually received.

The scroll-preservation fix (`apply_rename`/`remove_paths` avoiding
`_rebuild_all()`) was verified by spying on `FolderTreeCtrl.DeleteAllItems`
to confirm it's never called by either method, and - a stronger check than
"the visible scroll looks unchanged" - confirming two unrelated, untouched
rows' `_Node.wx_item` objects retain the exact same identity (`is`, not just
equal) before and after both a delete and a rename elsewhere in a 50-row
listing, proving neither operation tears down and rebuilds the tree at all,
which is what was actually resetting the scroll position back to the top
before this fix.

The command-line target was verified against a real `MainFrame` (with
`app.frame.get_connection` monkeypatched to an in-memory sqlite connection,
so the test never touched the real `~/.my-file-viewer` database) and real
temp folders/files: an absolute folder path opened it directly; an absolute
file path opened its parent folder with the file selected; a relative path
(with the process's cwd temporarily changed to a known directory) resolved
correctly against that cwd; a nonexistent path fell back silently to the
last-folder-or-home logic with no error popup; and passing no path at all
preserved the original startup behavior unchanged.

The Properties dialog was verified against a real `wx.App` and real temp
files/folders, both directly (`FileSystemService.get_properties`/
`calculate_folder_size` called and asserted against known values - a file's
exact size/extension/permissions, a folder's recursive size across a small
multi-file tree) and through the real `PropertiesDialog`: a file's Size
field showed its real size immediately (no "Calculating..." step, since
there's nothing to walk); a folder's Size field read "Calculating..."
immediately after the fast fields populated, then updated to the real
recursive total. Critically, with `calculate_folder_size` monkeypatched to
sleep for 600ms, a real blocking `ShowModal()` call was driven with a
`wx.CallLater`-scheduled poll (the same "schedule a callback, then
`ShowModal()`, then let the callback `EndModal()` it" technique
`SettingsDialog` was already verified with) - confirming the poll's timer
kept firing on its 50ms schedule for the full wait (proving the UI thread
was never blocked by the calculation) and that "Calculating..." was
genuinely observed before the real total replaced it. `show_properties_for_selected`
was confirmed to no-op for zero or multiple selected rows and open the
dialog only for exactly one, matching Open/Rename's own rule; the context
menu's and File menu's "Properties" item enabled state were confirmed to
follow the same `count == 1` rule as Open/Rename. The copy-path button was
confirmed to put the dialog's exact path on the clipboard, same mechanism
as the breadcrumb's own copy button.

## What's next (not built yet)

- A file-type column and complex glob-based selection (mentioned in the initial
  spec as planned future columns/features).
- Recursive folder size is now available on demand (Properties dialog), but
  the folder contents tree's own Size column still always reads "-" for a
  folder - showing it there for every visible folder row would mean an
  async recursive walk per row, not just per Properties click, so it's left
  as a possible future column rather than done as a side effect of this
  feature.
- Packaging beyond the PyInstaller `.spec`/GitHub Actions build - no AUR
  `PKGBUILD` and no `docs/my-file-viewer/` GitHub Pages homepage yet, unlike the
  sibling apps (neither was part of the initial spec for this project).
