# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is (and what it isn't yet)

A wxPython desktop app to visualize disk usage in a folder recursively - which
subfolders and which file types are eating the most space - so the user can go
delete the right thing. **This is a work in progress: only the data layer (SQLite
cache + migrations, `du` CLI wrapper) exists so far - there is no UI yet, and no
`main.py`/`app/frame.py` to run.** See "What's next" at the bottom for the planned
UI (drill-down table + pie chart), which will build on top of everything described
here.

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

# Run - not yet possible, see "What's next"
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

### Verification performed

There's no committed test suite yet (see "Commands"), but before considering the
data layer done, a scratch script built a real directory tree in `/tmp` (nested
folders, an empty directory, files with distinct extensions, a symlink) and ran
every repository method against it end to end, asserting byte totals computed
*independently* via `os.stat` matched what the code under test produced. It
caught the symlink double-counting bug described above. A second standalone
check (chmod a subdirectory to `000`) confirmed the partial-failure/`warnings`
path really does surface a permission error without discarding the rest of the
scan. Both were run, passed, and then deleted - they were scratch verification,
not fixtures worth keeping around without a real test framework in place.

## What's next (not built yet)

- `app/models.py`, `app/formatting.py` exist and are ready for UI code to import
  (`Entry`, `ExtensionUsage`, `format_bytes`).
- `app/frame.py` (composition root: open connection, run migrations, build
  `DiskScanRepository`/`CacheRepository`/`SettingsRepository`) and `main.py` -
  don't exist yet.
- Planned UI (per the human's own UX decisions): a single main page (no sidebar -
  unlike `my-docker-viewer`'s five resource types, this app has one concept) with
  a toolbar (Open Folder, breadcrumb, Reload, Reveal-in-file-manager - **read-only,
  no delete from the app**), a drill-down `wx.ListCtrl` table of the current
  folder's immediate children sorted by size, and a hand-drawn `wx.GraphicsContext`
  pie chart (no new dependency - same precedent as `my-docker-viewer`'s
  `sidebar.py` hand-drawing its own network icon) toggling between "by subfolder"
  and "by file type" (`CacheRepository.extension_breakdown`).
- Per-subdirectory scan jobs should use `run_background` (`app/async_task.py`),
  not `AsyncTaskRunner` (which is single-flight) - same reasoning as
  `ContainersDiskPage`'s Calculate button.
