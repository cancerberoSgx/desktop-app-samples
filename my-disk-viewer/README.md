# My Disk Viewer

A desktop app, built with [wxPython](https://wxpython.org), to visualize disk usage
in a folder recursively - which subfolders and which file types are eating the
most space - so you can find what's actually worth deleting to free space up.

**Status: work in progress, but runnable.** The data layer (SQLite cache +
migrations, the `du` CLI wrapper that does the actual scanning) and the wxPython
UI (breadcrumb + drill-down table, Reload, and a pie chart tab) are built and
verified - see `CLAUDE.md` for the full architecture and what's still ahead
(recent-folders list, packaging).

## Project layout

```
app/
  frame.py                     Composition root: DB connection + migrations,
                               builds the repositories, hosts ExplorerPage
  explorer_page.py             The one main screen: toolbar, breadcrumb,
                               drill-down table, and the Chart tab
  pie_chart.py                 PieChartPanel - hand-drawn pie + legend (no
                               charting library), toggled between "by subfolder/
                               file" and "by file type"
  disk_scan_repository.py     DiskScanRepository - wraps the `du` CLI (Linux/macOS);
                               list_immediate() is a cheap non-recursive os.scandir
                               pass, scan_subdirectory() runs `du -a -k -x` and
                               recursively covers a subdirectory's entire subtree
  cache_repository.py         CacheRepository (SQLite read/write for the folders/
                               files cache) + SettingsRepository
  models.py                   Entry, ExtensionUsage, ScannedFile, ScannedDir,
                               SubtreeScan, ImmediateListing dataclasses
  formatting.py                format_bytes - human-readable byte counts
  async_task.py                AsyncTaskRunner / run_background - runs scans off
                               the UI thread (copied from my-docker-viewer, generic)
  db/
    paths.py                  Resolves ~/.my-disk-viewer and the migrations folder
    connection.py              SQLite connection factory
    migrator.py                 Applies any new *.sql file under db/migrations/
    migrations/
      0001_create_scan_tables.sql   folders/files cache tables + indexes
      0002_create_settings.sql       key/value settings table
main.py                        Entry point - `python3 main.py [folder]`
requirements.txt
```

## Why `du` for scanning

`du -a -k -x <path>` ships on every Linux and macOS system already (no extra
install), and reports real allocated disk usage rather than a file's
apparent/logical size - the number that actually answers "what's eating my disk".
See `CLAUDE.md` for the full comparison against `ncdu`/`dua`/`dust`/`gdu` and a
pure-Python walker, and why `du` won for this app. Windows has no `du` equivalent
and is intentionally out of scope for now.

## How scanning + caching work

Disk usage stats for every file and folder are cached in SQLite
(`~/.my-disk-viewer/my-disk-viewer.db`) so revisiting a folder is instant. One
`du` call recursively covers an entire subdirectory's contents in one shot - not
just its immediate children - so "reload" a folder rescans just its direct
children (in parallel, one job per subdirectory) and that single pass already
populates every descendant underneath them too. Drilling further down into an
already-scanned subfolder is a plain cache read, no rescan, until you explicitly
hit Reload on it again.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python3 main.py                # then use "Open Folder..." in the toolbar
python3 main.py /some/folder   # or open straight into a folder
```

Use **Reload** to compute disk usage for the open folder - it's never automatic
(even the first time), since it means running `du` against every immediate
subdirectory. The **Chart** tab shows the same data as the table as a pie, toggled
between "by subfolder/file" and "by file type" (recursive, by extension).

## Installing on Arch Linux (AUR)

There's an `aur/PKGBUILD` in this directory that installs `mydiskviewer` as a
normal, dynamically-linked Arch package (`python` + `python-wxpython` from
the official repos, nothing bundled) - lighter on disk and memory than a
PyInstaller build, since it shares the system's already-installed
Python/wxWidgets instead of vendoring a private copy of each. See `/AUR.md`
at the repo root for how to test it locally and publish it to AUR.
