# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app for managing Redis connections: users pick a **profile**, and
everything else (currently just data sources) belongs to that profile. A data source
holds the fields needed to connect to a Redis server (`name`, `redis_host`,
`redis_port`, `redis_user`, `redis_password`). "Connect" opens a real connection via
redis-py and issues a `PING`, reporting success or failure - there is intentionally no
data-exploration UI (no key browsing, no command console).

This project was templated from the sibling `my-data-viewer` app - same overall
architecture (composition root, repository pattern, migrations, profiles-scope-
everything), but with the CSV/JSON/Postgres datasource machinery and the entire Data
Explore page (SQL editor, table/column/index browsing, Parquet export) stripped out
and replaced by a single Redis connection type with a PING-only "Connect".

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
.venv/bin/pyinstaller --noconfirm myredisviewer.spec
./dist/myredisviewer/myredisviewer    # the runnable output - see packaging gotchas below
```

There is no linter or test suite configured in this repo yet.

## Architecture

### Startup wiring (`app/frame.py`)

`MainFrame.__init__` does all composition-root work: opens the single sqlite3
connection (`app/db/connection.py`), runs pending migrations against it
(`app/db/migrator.py`), builds the repositories on top of that one connection, then
resolves which profile is active before building any page. There is no dependency
injection framework - everything is wired by hand in this one place.

### Profiles scope everything

- `MainFrame._bootstrap_active_profile()` runs on every startup: if zero profiles
  exist it silently creates one named `"default"` (no prompt to the user - this was
  an explicit product decision, don't add one back without checking). It then
  restores whichever profile id was last saved in the `settings` key/value table,
  falling back to the first profile if that stored id no longer exists.
- The active profile id lives in-memory as `MainFrame.active_profile_id` and is
  threaded into `DatasourcesPage` at construction time. `ProfilesPage` never touches
  datasources directly - activating a profile calls `MainFrame._on_activate_profile`,
  which updates `settings` and calls `DatasourcesPage.set_profile(...)` to reload.
- `MainFrame._on_profiles_changed()` is called after any create/edit/delete on the
  Profiles screen and re-validates that the active profile still exists (deleting a
  profile cascades to its datasources via the `profile_id` FK's `ON DELETE CASCADE`;
  deleting the last remaining profile re-triggers the same auto-create-"default" path
  as a cold start).
- Any new concept that's meant to belong to a profile needs its own `profile_id`
  column and must be scoped through the same pattern (repository method takes
  `profile_id`, page is constructed with the current profile and exposes
  `set_profile()`).

### Repository pattern

One `<Concept>Repository` class per concept (`ProfileRepository`,
`DatasourceRepository`, `SettingsRepository`, all in `app/repositories.py`), each
doing plain SQL against the shared `sqlite3.Connection` (row_factory is
`sqlite3.Row`). This is the established convention - new concepts should follow it
rather than introducing an ORM or a different data-access style.

`DatasourceRepository` is pure CRUD against the local `datasources` sqlite table
(scoped by `profile_id`), plus one "live" operation - `test_connection`, which opens
a `redis.Redis(...)` client with the record's host/port/user/password and calls
`.ping()`. It raises on any failure (connection refused, auth error, timeout); the
caller (`DatasourcesPage._on_connect`) runs it through `AsyncTaskRunner` (see below)
and shows a message box on success or failure. There is no per-type driver dispatch
here (unlike `my-data-viewer`'s `drivers.py`) because there is only one connection
type.

### Blocking Redis calls must go through `AsyncTaskRunner` (`app/async_task.py`)

wxPython has a single UI thread - any blocking call made directly from an event
handler (a `redis-py` command, `test_connection`, anything that hits the network)
freezes the whole window until it returns. Redis commands can take several seconds
and future ones may return large results, so **every repository method that talks to
Redis must be invoked through `AsyncTaskRunner`, never called synchronously from a
`wx.EVT_*` handler.** This is the established pattern - do not add a new blocking
call site that skips it, and do not reach for `asyncio`/`wxasync` instead (see
below for why).

- One `AsyncTaskRunner` instance per page/dialog, created once in `__init__` (e.g.
  `self._async = AsyncTaskRunner(self)`) and reused for every blocking action that
  screen offers - see `DatasourcesPage.__init__` and `DatasourcesPage._on_connect`
  for the reference usage.
- Call `self._async.run(work=..., on_success=..., on_error=..., disable=[...])`:
  - `work` is a zero-arg callable that does the actual blocking repository call
    (wrap it in a `lambda` to close over arguments, as `_on_connect` does).
  - `on_success(result)` / `on_error(exc)` run back on the UI thread afterwards -
    this is where message boxes, list reloads, or result rendering belong.
  - `disable=[...]` should list the triggering button(s) so they're disabled while
    the call is in flight and re-enabled after - always pass this rather than
    hand-rolling `Enable(False)`/`Enable(True)` around the call.
- `AsyncTaskRunner` ignores a second `run()` call while one is already in flight on
  that instance, so accidental double-clicks/list-activations can't stack
  overlapping Redis commands - don't add your own re-entrancy guard on top of it.
- It's built on `wx.lib.delayedresult.startWorker` (thread + `wx.CallAfter` under
  the hood), not `asyncio`/`wxasync` - that tradeoff was deliberate (thread pattern
  works with the synchronous `redis-py` client already in use, needs no new
  dependency, and doesn't require recoloring the repository layer `async`). Keep
  using it for new Redis operations rather than introducing a second concurrency
  model.
- Commands that can return a lot of data (future `SCAN`-style listing/browsing,
  once added) should still render through `work`/`on_success` as above, but favor
  cursor-based iteration over one giant blocking call, and populate any resulting
  `wx.ListCtrl`/`DataViewListCtrl` in virtual mode rather than inserting every row -
  large-result rendering on the UI thread can freeze the window just as much as the
  network call it replaces.

### Migrations (`app/db/migrations/*.sql`)

Schema changes are made by **adding a new numbered `.sql` file** (next sequence
number, e.g. `0004_...sql`) - never edit an already-applied migration.
`run_migrations()` applies any file not yet recorded in `schema_migrations`, in
filename order, once per file. `app/db/paths.py` resolves the migrations directory
via `sys._MEIPASS` when frozen so it works both from source and from a PyInstaller
build - **any new migration file must also be picked up by the existing
`datas=[('app/db/migrations', 'app/db/migrations')]` glob-style entry in
`myredisviewer.spec`** (it already covers the whole directory, so no spec change is
needed unless the directory itself moves).

### UI structure

Left `Sidebar` (icon buttons, `app/sidebar.py`) drives a `wx.Simplebook` in
`MainFrame` - `SIDEBAR_ITEMS` order must match the order pages are added to the book
(`Sidebar._on_button_clicked` selects by index): Profiles (0), Data Sources (1),
About (2). Each screen is a `<concept>_page.py` (list/filter/CRUD toolbar built on
`wx.ListCtrl`) + `<concept>_dialog.py` (modal `wx.Dialog` create/edit form) pair -
see `profiles_page.py`/`profiles_dialog.py` and `datasources_page.py`/
`datasources_dialog.py` for the pattern to follow when adding a new concept's screen.
Unlike `my-data-viewer`, there is no extra "Connect" destination page - Connect just
shows a message box on the same screen.

### PyInstaller packaging gotchas (see my-data-viewer's git history for the original incident)

- **Always build with this project's own venv's pyinstaller**
  (`.venv/bin/pyinstaller`), never a bare `pyinstaller` resolved from `PATH` - it can
  silently resolve to a *different* project's venv that lacks `redis`, producing a
  build that fails at runtime with `ModuleNotFoundError: No module named 'redis'`.
- **Build via `pyinstaller --noconfirm myredisviewer.spec`**, not
  `pyinstaller ... main.py` - the latter regenerates/overwrites `myredisviewer.spec`
  from scratch, silently wiping the `datas` entry that bundles the SQL migrations.
- **The runnable output is `dist/myredisviewer/myredisviewer`.** `build/` is
  PyInstaller's intermediate scratch directory and is never a complete, runnable
  tree - executing anything under it will fail to find shared libraries.
