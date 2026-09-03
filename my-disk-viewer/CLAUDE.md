# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app to visualize disk usage in a folder recursively - which
subfolders and which file types are eating the most space - so the user can go
delete the right thing. Read-only: it never deletes anything itself, only reveals
a file/folder in the OS file manager. Runnable end to end: `python3 main.py
[folder]` opens the breadcrumb + drill-down table screen, with a **Reload** button
that scans (never automatic) and a **Chart** tab showing the same folder as a pie.
See "What's next" at the bottom for what's still ahead (recent-folders list,
packaging) and "Verification performed" for how the scan/cache/UI pipeline and the
chart were actually checked - both a real temp-directory scan and an off-screen
chart render, not just written-and-assumed-correct.

This project was templated from the sibling `my-docker-viewer` app for its overall
architecture (SQLite + migrations under `app/db/`, `AsyncTaskRunner`/`run_background`
facade for running blocking CLI calls off the UI thread, a CLI-wrapping repository
that never raises a raw traceback) - but **no feature was copied**: there are no
containers/images/volumes/networks, no profiles, no datasources. The only thing
carried over verbatim is `app/async_task.py`, which is generic infrastructure with
nothing docker-specific in it.

## Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python3 main.py                # then use "Open Folder..." in the toolbar
python3 main.py /some/folder   # or open straight into a folder - also how the
                                # scratch verification scripts drove it end to end
```

There is no linter or test suite configured in this repo yet. The data layer was
verified with a standalone scratch script (built a known directory tree, ran every
repository method against it, asserted expected byte totals/counts) rather than a
committed test suite - see "Verification performed" below for what that covered.

## Architecture

### Why `du`, not a Python-only walker or another tool (`ncdu`/`dua`/`dust`/`gdu`)

`app/disk_scan_repository.py`'s module docstring has the full comparison. Short
version: `du -a -k -x <path>` ships on every Linux and macOS system already (GNU
coreutils / BSD du both support this exact flag combination identically, no
OS-specific branch needed) and reports **real allocated disk usage**
(`st_blocks`-equivalent), not a file's apparent/logical size - which is the number
that actually answers "what's eating my disk" (sparse files, block rounding,
hardlinks all show up correctly). `ncdu`/`dua`/`dust`/`gdu` are all faster or
friendlier on huge trees but none of them ships by default anywhere, unlike `du` -
using one would mean bundling a per-platform binary, the exact tradeoff
`my-docker-viewer` avoided by shelling out to `docker` instead of vendoring a
Docker SDK. A pure-Python `os.scandir`/`os.stat` recursive walker was the other
serious option (fully portable, including to a future Windows backend where
there's no `du` at all) - `list_immediate` below uses exactly that, but only for
one folder's *immediate* children; the expensive recursive walk stays delegated to
`du`, which does it faster than a Python loop would.

**Windows has no built-in `du` equivalent** and is explicitly out of scope for now
(per the human's own instruction) - when it's tackled, the natural fallback is
extending the pure-Python `list_immediate` approach into a full recursive walker
(`st_size` instead of `st_blocks`, no `du` dialect to match), swapped in behind
`DiskScanRepository`'s two methods without touching `CacheRepository` or the
schema - both already store/read plain `(path, size_bytes, is_dir)` shaped data
regardless of which backend produced it.

### The key trick: one `scan_subdirectory` call covers an entire subtree, not just one level

`du -a -k -x <path>` recursively walks **everything** under `path`, arbitrarily
deep - not just `path`'s immediate children. This is why "reload" is naturally
recursive and drilling down is (almost always) free, with no crawling logic
duplicated in Python:

- **Reloading a folder F** (a later UI step) scans only F's *immediate* children:
  direct files via `DiskScanRepository.list_immediate`'s cheap `os.stat` pass (no
  `du` needed - their size is already fully known), and each direct subdirectory
  via its own independent `scan_subdirectory` call - meant to run as its own
  `run_background` job per subdirectory once the UI exists, exactly
  `ContainersDiskPage._start_container_job`'s "every item sized by its own
  independent job, fastest renders first" pattern in `my-docker-viewer`.
- Because that one `du` call already recursed through the *whole* subtree, every
  descendant directory arbitrarily deep under F - not just F's direct children -
  lands in the cache with its own correct recursive total in the same pass (see
  `CacheRepository.replace_subtree`, and the smoke-test assertion that a
  *grandchild* directory is present after scanning only its grandparent).
- **Drilling into an already-scanned subfolder S later** is a plain SQLite read
  (`CacheRepository.list_children(S)`) - no rescan, because F's `du` run already
  covered S. The user only pays the `du` cost again by explicitly reloading some
  folder - "cache in SQLite, reload recursively on demand" exactly as specified.
- The one thing a scan can never produce on its own: the row for the folder the
  user actually opened (call it F) - it's `du`'s *caller*, never its argument, so
  its own total is the sum of its already-scanned children, written via
  `CacheRepository.upsert_folder_summary` once every child job finishes (an
  ordinary SQLite upsert, not a scan result).

### `app/disk_scan_repository.py` - the `du` CLI wrapper

- `DiskScanRepository.list_immediate(folder_path)` - cheap, non-recursive
  `os.scandir` pass, no `du` call. Direct files are fully sized here via
  `os.stat().st_blocks * 512` (real allocated bytes, matching `du`'s own
  definition, not `st_size`); direct subdirectories come back as bare paths only.
- `DiskScanRepository.scan_subdirectory(path)` - the expensive half, one `du -a -k
  -x` call parsed into a `SubtreeScan` (every file + every directory anywhere
  under `path`, with directories' sizes already being `du`'s own recursive
  totals).
- **Scan-scope defaults, applied identically by both methods**: stay on one
  filesystem (`-x` for `du`; `st_dev` comparison for `list_immediate`) and never
  follow symlinks - both skip them outright rather than sizing them, so a folder's
  total doesn't depend on which of the two computed it. This was **not** free:
  `du -a` without `-L`/`-D` doesn't *follow* a symlink into its target, but it
  still *prints* the link itself as its own tiny entry (that's what `-a` means -
  list every entry, symlinks included) - the smoke test caught this as a real
  double-counting bug (a symlinked `.txt` file was inflating that extension's
  total and file count) before `_build_subtree_scan` learned to filter
  `os.path.islink()` entries out before the is-dir/item-count steps, not after -
  a symlink to a *directory* would otherwise stat as `is_dir=True` with the
  *link's* tiny size mistaken for a real recursive total.
- **A `du` run that partially fails does not raise** - `_run_du` only raises
  `DuCommandError` when stdout comes back completely empty (the target itself
  doesn't exist or is entirely unreadable). The far more common case - `du`
  couldn't read *one* descendant (permission denied) but produced correct results
  for the rest of a large subtree - comes back as `(stdout, warnings)`, both
  verified directly against a real chmod'd-000 directory: the rest of the subtree
  still landed correctly and `warnings` carried `du`'s own stderr, not a hard
  failure. This mirrors `DiskUsageRepository.sum_mounts_bytes`'s "one bad mount
  doesn't blank out an otherwise-good number" posture in `my-docker-viewer`.
- `item_count` (files anywhere recursively under a directory) isn't something
  `du` reports - `_build_subtree_scan` derives it bottom-up in one O(n) pass over
  paths ordered deepest-first via a parent→children index built once, rather than
  a substring-prefix scan per directory (which would be O(n·d) on large trees).

### `app/cache_repository.py` - the SQLite cache, and only place that knows the schema

- `CacheRepository` is the sole reader/writer of the `folders`/`files` tables
  (`app/db/migrations/0001_create_scan_tables.sql`) - `DiskScanRepository` never
  touches SQLite, and no future UI code should write SQL directly against these
  tables either.
- **This is a cache, not a database of record**: every row is only ever produced
  by re-running `DiskScanRepository` and replacing what was there.
  `replace_subtree(scan)` deletes the entire old subtree (`path = ? OR path GLOB
  ?`) before inserting the fresh result, specifically so a file deleted from disk
  since the last scan actually disappears from the cache instead of lingering -
  verified in the smoke test (removed a file on disk, rescanned its parent,
  confirmed it dropped out of `list_children`).
- **GLOB, not LIKE, for every "everything under this path" query**
  (`extension_breakdown`, `_delete_subtree`). A filesystem path can legally
  contain `%`/`_`, which SQL `LIKE` treats as wildcards and would need per-query
  escaping to use safely; `GLOB`'s wildcards are `*`/`?` instead, so a real path's
  own characters are never misinterpreted. Both are equally indexed-prefix-
  scannable in SQLite as long as the pattern has no leading wildcard, so this
  costs nothing over `LIKE` even on a large cached subtree.
- `prune_missing_children(folder_path, existing_paths)` is deliberately *not*
  "delete everything then let replace_* reinsert it" - it only removes rows whose
  name is no longer present at all, leaving rows for names still present
  untouched until their own fresh scan overwrites them. This matters because a
  folder's immediate listing (`list_immediate`) is cheap and safe to refresh
  often, while each child subdirectory's actual total only refreshes once its own
  (expensive) `scan_subdirectory` job completes - deleting a subdirectory's cached
  total just because the *parent* was relisted would blank out a real number for
  no reason.
- `SettingsRepository` is the same key/value pattern as `my-docker-viewer`'s -
  wired up (`0002_create_settings.sql`) but not read/written by anything yet;
  reserved for a recent-folders list once the "Open Folder" toolbar exists.

### Migrations (`app/db/migrations/*.sql`)

Same mechanism as `my-docker-viewer`: add a new numbered `.sql` file, never edit
an already-applied one. `0001_create_scan_tables.sql` creates `folders`/`files`
with indexes on `parent_path` (drill-down queries) and `files.extension` (chart
queries); `0002_create_settings.sql` is the key/value table. Data lives in
`~/.my-disk-viewer/my-disk-viewer.db`.

### Version (`app/version.py`, `VERSION`)

`app/version.py::get_version()` reads the plain-text `VERSION` file at the
project root (via `app/db/paths.py::project_root()` - the same
frozen-vs-source resolution the migrations dir uses) and is what the
Help > About menu item displays (`MainFrame._on_about`). There is no
sidebar/About page in this app to also show it (see "`app/frame.py` -
composition root" below) - the menu item is the only About surface. It is
**not** derived from git (`git describe` etc.) - a PyInstaller build has no
`.git` directory to read that from at runtime, so `VERSION` is baked in at
build time instead. Anywhere this file needs to be read from, it must also
be **bundled alongside `main.py`/`app/`**:
- `mydiskviewer.spec`'s `datas=[...]` includes `('VERSION', '.')`.
- `aur/PKGBUILD`'s `package()` copies `VERSION` next to `main.py` under
  `/usr/lib/mydiskviewer/`, same as it does for `main.py` itself.

See the root `README.md`'s "Versioning" section and `AUR.md` for how
`VERSION` gets bumped (`scripts/bump-app-version.py`) and how it flows into
release tags, GitHub Actions artifact filenames, and the AUR package's
`pkgver`.

### `app/frame.py` - composition root

Same posture as `my-docker-viewer`'s: opens the one sqlite3 connection, runs
migrations, builds `DiskScanRepository`/`CacheRepository`/`SettingsRepository`,
hosts `ExplorerPage`. **No sidebar/`wx.Simplebook`** - unlike `my-docker-viewer`'s
five resource-type screens, this app has one concept, so `ExplorerPage` fills the
window directly.

### `app/explorer_page.py` - the one main screen

Toolbar (Open Folder, Up, Reload, Reveal-in-file-manager) + a clickable breadcrumb
+ a `wx.Notebook` with a **Table** page (the drill-down `wx.ListCtrl`) and a
**Chart** page (`PieChartPanel` + a mode toggle) - both views read the same
`self._entries`, kept in sync in one place (`_update_chart`, called from the end
of `_populate_list`), so they can never drift apart.

- **Uses `run_background` for every async call on this page, not
  `AsyncTaskRunner`** - a deliberate deviation from `my-docker-viewer`'s pattern,
  not an oversight. `AsyncTaskRunner`'s single-flight/`disable`-then-re-enable
  bookkeeping is built for one task bound to specific widgets, but this page's
  busy period during Reload spans *two* phases (a listing call, then N concurrent
  `scan_subdirectory` jobs) - `AsyncTaskRunner.run`'s `on_done` would re-enable
  the toolbar the moment the first phase finished, while subdirectory jobs were
  still in flight. Both `_load_current_folder` (navigation) and `_on_reload` use
  `run_background` directly with manually-tracked `self._loading`/`self._reloading`
  flags and `_update_button_states()`, exactly `ContainersDiskPage.Calculate`'s
  own shape in `my-docker-viewer`. `AsyncTaskRunner` stays in `app/async_task.py`
  for a future dialog that actually is bound to specific widgets.
- **Critical thread-safety invariant: every `CacheRepository` call happens inside
  a `success`/`error` callback, never inside a `work()` callable.** `work()` runs
  on a background thread (`wx.lib.delayedresult`); `success`/`error` are
  guaranteed back on the main GUI thread via `wx.CallAfter`. sqlite3 connections
  aren't safe to share across threads, and Reload can have several
  `scan_subdirectory` jobs in flight at once - keeping every read/write inside the
  callbacks (which wx's event loop processes one at a time) makes this safe
  without a lock. `DiskScanRepository` has no mutable state, so its methods are
  the only thing that ever runs inside `work()`.
- **`MAX_CONCURRENT_SCAN_JOBS` (4) bounds how many `du` subprocesses one Reload
  runs at once**, via a plain queue (`_scan_queue`/`_start_next_queued_job`) rather
  than a semaphore class - same motivation as `DiskUsageRepository`'s
  `MAX_CONCURRENT_DU_RUNS` in `my-docker-viewer`: a folder with hundreds of
  immediate subdirectories shouldn't spawn hundreds of simultaneous `du` walks,
  which thrashes I/O rather than finishing faster.
- **A subdirectory rescan failing outright doesn't blank its previous good
  total** - `_start_subdir_job`'s `error` callback reads the existing cached entry
  first and keeps its `size_bytes`/`item_count`, only adding the new error note.
  Same "one bad rescan doesn't erase a real number" posture as
  `DiskUsageRepository.sum_mounts_bytes`.
- **Opening a folder shows whatever's cached instantly**, then a background
  `list_immediate` call refines it (new/removed children since last time) -
  `_merge_with_cache` builds display `Entry` rows for every subdirectory
  currently on disk even if it's never been scanned (shown as "Not scanned"
  immediately, not invisible until the next Reload).
- Every async callback closure captures the folder `path` it started for and
  checks it against `self._current_path` before touching shared state - guards
  against a stale background result landing after the user already navigated
  elsewhere.
- **Reveal-in-file-manager** (`_reveal_in_file_manager`) is the one function with
  an OS-specific branch in this codebase - `open`/`xdg-open`/`explorer` per
  platform - since there's no single cross-platform CLI for it, unlike the
  du/scan layer. Failures are swallowed (best-effort convenience action, not a
  core feature).

### `app/pie_chart.py` - `PieChartPanel`, hand-drawn, no charting library

Same "draw it ourselves" precedent as `my-docker-viewer`'s `sidebar.py` hand-
drawing its own network icon - built against the **dataviz skill**'s method
rather than eyeballed colors/layout:

- **Caps real slices at 6, folding the rest into a gray "Other"** - a pie
  specifically caps lower than the general categorical ladder (7-8 for bars/
  lines); past ~6 wedges blur together at a glance (`anti-patterns.md`). "Other"
  is gray, not a 7th hue - it isn't a real identity, just a fold-in bucket.
- **0 items renders a message; exactly 1 item renders as plain text, not a full
  circle** - a one-slice "pie" has nothing to compare (the same reasoning
  `anti-patterns.md` gives for why a 2-slice pie should be a stat tile instead,
  just more so).
- **Colors are the first 6 slots of the skill's validated 8-hue categorical
  default, assigned in fixed order by rank** (biggest = slot 1/blue), with a
  light AND dark variant - `_is_dark()` reads the panel's actual
  `GetBackgroundColour()` at draw time and picks the matching validated set,
  since this app follows the OS/GTK theme rather than toggling its own. Both
  6-hue subsets were run through the skill's validator
  (`validate_palette.js "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300"
  --mode light --surface "#fcfcfb"` and the dark-hex equivalent against
  `#1a1a19`) - both pass every hard check; light mode gets a WARN that 3 fills
  (aqua/yellow/magenta) sit below 3:1 contrast against the light surface, which
  the skill says requires a "relief channel" (visible labels or a table view) -
  satisfied structurally here since the **Table** tab next to **Chart** shows the
  exact same data as real numbers, always.
- **Wedges are drawn as manually-computed line-segment polygons, not
  `GraphicsPath.AddArc`** - `AddArc`'s angle/clockwise convention is easy to get
  backwards in a y-down screen coordinate system; plain trigonometry
  (`_wedge_path`) is unambiguous and was verified correct by rendering and
  visually checking the output (see "Verification performed") rather than
  reasoned about abstractly.
- **Legend always present, text never colored by a slice's own hue** (only the
  swatch carries the hue - `marks-and-anatomy.md`'s "text never wears the data
  color"). **Direct label on the single largest wedge only** (sparing labeling),
  skipped entirely if even that wedge's angular sweep is too thin to hold text
  without spilling past it - the legend still carries every value regardless.
- **Hover highlights the wedge and shows a tooltip** (`_on_motion`/`_set_hover`) -
  the skill's "ship the hover layer by default" rule for an interactive chart.
- `ExplorerPage._update_chart()` feeds it either `self._entries` (mode
  "children" - same items the table shows) or a fresh
  `CacheRepository.extension_breakdown()` call (mode "extension") every time the
  table repopulates, so the chart is never a stale second copy of the table's
  numbers - both were the human's own UX decision (a toggle between the two
  breakdowns, not one fixed mode).

### Migrations (`app/db/migrations/*.sql`)

Same mechanism as `my-docker-viewer`: add a new numbered `.sql` file, never edit
an already-applied one. `0001_create_scan_tables.sql` creates `folders`/`files`
with indexes on `parent_path` (drill-down queries) and `files.extension` (chart
queries); `0002_create_settings.sql` is the key/value table. Data lives in
`~/.my-disk-viewer/my-disk-viewer.db`.

### Verification performed

There's no committed test suite yet (see "Commands") - everything below was
scratch-verified, run, and then deleted, not kept as fixtures without a real test
framework in place.

- **Data layer**: a scratch script built a real directory tree in `/tmp` (nested
  folders, an empty directory, files with distinct extensions, a symlink) and ran
  every repository method against it end to end, asserting byte totals computed
  *independently* via `os.stat` matched what the code under test produced. It
  caught the symlink double-counting bug described above. A second check
  (chmod a subdirectory to `000`) confirmed the partial-failure/`warnings` path
  really does surface a permission error without discarding the rest of the scan.
- **UI/orchestration layer**: rather than simulate mouse clicks on a real window
  (tried once, landed on the wrong control due to a window-manager decoration
  offset, and risks touching the live desktop the assistant runs on) - the real
  fix was driving `ExplorerPage`'s actual event handlers (`open_folder`,
  `_on_reload`, `_on_chart_mode_changed`) directly in-process against a real
  `wx.App`/`MainFrame`, pumping the event loop (`wx.YieldIfNeeded`) so the
  background threads' `wx.CallAfter` callbacks actually run, then asserting on
  the resulting `Entry`/chart state. This exercised the full open → Reload → N
  concurrent `scan_subdirectory` jobs → cache-write → drill-down-reads-from-
  cache-with-no-rescan → `extension_breakdown` pipeline against a real 18MB test
  tree (videos/photos/code/node_modules/logs, plus a symlink) end to end.
- **Chart rendering**: `PieChartPanel._draw` was called directly against an
  off-screen `wx.Bitmap`/`wx.MemoryDC` (no window, no display interaction at all)
  and the PNGs visually inspected - covering the normal multi-slice case, the
  >6-items "Other" fold-in, the 0-item and 1-item edge cases, and both chart
  modes. This is the safe way to visually check custom `wx.GraphicsContext`
  drawing: it never touches a real window, so there's no risk of the kind of
  misclick that ended the mouse-automation attempt above.

## What's next (not built yet)

- A recent-folders list for the "Open Folder" toolbar, backed by
  `SettingsRepository` (wired up, unused so far).
- Packaging (`.spec` file for PyInstaller, following `my-docker-viewer`'s
  `datas=[('app/db/migrations', ...)]` gotcha).
- Windows support (explicitly out of scope per the human's own instruction) -
  see `disk_scan_repository.py`'s module docstring for the planned approach
  (extend `list_immediate`'s pure-Python `os.scandir` walker into a full
  recursive one, `st_size` instead of `st_blocks`).
