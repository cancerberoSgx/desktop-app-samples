# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app for admin'ing local Docker containers: list every container
(running and stopped) with its status, image, size, and live CPU/memory usage,
filter the list by name/image/status, and stop or remove the selected one.

This project was templated from the sibling `my-redis-viewer` app for its overall
architecture (composition root in `frame.py`, sidebar + `wx.Simplebook`, `AsyncTaskRunner`
facade, SQLite + migrations) - but **no feature was copied**: there are no profiles, no
datasources, no data-explorer concept. The one screen this app has (Containers) is a
new concept built from scratch on top of the docker CLI.

**There is no docker SDK/Engine API dependency.** Every docker operation - listing,
stats, stop, remove - shells out to the `docker` binary and parses its
`--format '{{json .}}'` output (`app/repositories.py::ContainerRepository`). This was
an explicit choice: it needs nothing beyond a working Docker install already on PATH,
matches what `docker` itself reports, and avoids pinning to a specific docker-py
version/API compatibility matrix.

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
.venv/bin/pyinstaller --noconfirm mydockerviewer.spec
./dist/mydockerviewer/mydockerviewer  # the runnable output - see packaging gotchas below
```

There is no linter or test suite configured in this repo yet.

## Architecture

### Startup wiring (`app/frame.py`)

`MainFrame.__init__` does all composition-root work: opens the single sqlite3
connection (`app/db/connection.py`), runs pending migrations against it
(`app/db/migrator.py`), builds `SettingsRepository` on top of that connection, and
constructs a plain `ContainerRepository()` (no connection/state - it's a stateless
CLI wrapper). There is no dependency injection framework - everything is wired by
hand in this one place, same as `my-redis-viewer`.

### `ContainerRepository` (`app/repositories.py`) - the docker CLI wrapper

- `list()` runs `docker ps -a --size --format '{{json .}}'` (one JSON object per
  line - identity, status, size) and merges it with `docker stats --no-stream
  --format '{{json .}}'` (live CPU/memory, keyed by container ID) - `docker stats`
  only reports running containers, so stopped ones simply keep `cpu_percent` /
  `mem_usage` / `mem_percent` as `None`.
- `stop(container_id)` / `remove(container_id, force=...)` shell out to
  `docker stop` / `docker rm [-f]` directly - no dry-run, no undo.
- Every call goes through the private `_run()` helper, which distinguishes two
  failure modes callers must handle differently:
  - `DockerNotAvailableError` - the `docker` binary itself isn't on PATH (raised
    from a `FileNotFoundError`). This is the "fail if not installed" case - show a
    clear message, don't let it surface as a traceback.
  - `DockerCommandError` - `docker` ran but the command failed (daemon
    unreachable, no such container, permission denied, timed out) - message is
    docker's own stderr wherever available.
- New docker operations should follow this same pattern: build the argv list,
  call `self._run(args)`, parse `{{json .}}` output with `json.loads` per line (docker
  emits one JSON object per line, not a JSON array).

### Blocking docker CLI calls must go through `AsyncTaskRunner` (`app/async_task.py`)

Identical pattern and rationale to `my-redis-viewer`: wxPython has a single UI
thread, and `docker ps`/`docker stats`/`docker stop`/`docker rm` are all subprocess
calls that can take a noticeable moment (`docker stats --no-stream` in particular
has to wait out one sampling window). **Every repository call must be invoked
through `AsyncTaskRunner`, never called synchronously from a `wx.EVT_*` handler or
timer tick.**

- `ContainersPage` creates one `AsyncTaskRunner` instance in `__init__` and reuses
  it for `reload()`, `_on_stop()`, and `_on_remove()`.
- `AsyncTaskRunner.run()` ignores a second call while one is already in flight on
  that instance - this is what makes the auto-refresh timer (see below) safe to
  fire even if a manual refresh or a stop/remove is still running: `reload()`
  checks `self._async.is_busy()` itself before even attempting to call `run()`.
- Built on `wx.lib.delayedresult.startWorker`, not `asyncio` - keep using it for
  any new blocking docker call rather than introducing a second concurrency model.

### Auto-refresh

`ContainersPage` runs a `wx.Timer` (`AUTO_REFRESH_INTERVAL_MS`, currently 5s) that
calls `reload()` on every tick, since CPU/memory are point-in-time samples and go
stale immediately after a manual refresh. `reload()` re-renders the list from the
freshly-fetched data while preserving the current selection by container ID (see
`_populate_list`), so an in-flight auto-refresh doesn't fight with the user
selecting a row. The timer is stopped on `EVT_WINDOW_DESTROY` so it can't fire
against a torn-down page.

### Filtering

Name/image/status filters (`_name_filter`, `_image_filter`, `_status_choice` in
`ContainersPage`) are applied **client-side** against the last `list()` result
(`_filtered_containers()`) - no docker call is re-issued on every keystroke, only
`reload()` (manual refresh or the timer) hits the CLI. Status filtering matches
against docker's own `State` field (`running`, `exited`, `paused`, `restarting`,
`created`, `removing`, `dead`), not the human-readable `Status` string.

### Migrations (`app/db/migrations/*.sql`)

Schema changes are made by **adding a new numbered `.sql` file** (next sequence
number, e.g. `0002_...sql`) - never edit an already-applied migration.
`run_migrations()` applies any file not yet recorded in `schema_migrations`, in
filename order, once per file. Currently there is just `0001_create_settings.sql`
(a plain key/value table) - `SettingsRepository` is wired up in `frame.py` but not
yet read/written by any screen; it's there so a future preference (remembered
filters, refresh interval, ...) has a ready-made place to live. Any new migration
file is automatically picked up by the existing
`datas=[('app/db/migrations', 'app/db/migrations')]` glob-style entry in
`mydockerviewer.spec` - no spec change needed unless the directory itself moves.

### UI structure

Left `Sidebar` (icon buttons, `app/sidebar.py`) drives a `wx.Simplebook` in
`MainFrame` - `SIDEBAR_ITEMS` order must match the order pages are added to the
book (`Sidebar._on_button_clicked` selects by index): Containers (0), About (1).

### PyInstaller packaging gotchas (see my-redis-viewer's/my-data-viewer's git history for the original incident)

- **Always build with this project's own venv's pyinstaller**
  (`.venv/bin/pyinstaller`), never a bare `pyinstaller` resolved from `PATH` - it can
  silently resolve to a *different* project's venv, producing a build that's missing
  dependencies at runtime.
- **Build via `pyinstaller --noconfirm mydockerviewer.spec`**, not
  `pyinstaller ... main.py` - the latter regenerates/overwrites `mydockerviewer.spec`
  from scratch, silently wiping the `datas` entry that bundles the SQL migrations.
- **The runnable output is `dist/mydockerviewer/mydockerviewer`.** `build/` is
  PyInstaller's intermediate scratch directory and is never a complete, runnable
  tree - executing anything under it will fail to find shared libraries.
- This app also depends on the `docker` binary being present **on the machine
  running the built executable** (not the build machine) - PyInstaller bundles
  Python/wxPython, not Docker itself.
