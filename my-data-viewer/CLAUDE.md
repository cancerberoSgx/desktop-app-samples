# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app for exploring databases and CSV files: users pick a **profile**,
and everything else (currently just data sources) belongs to that profile. A data
source can be queried for its tables/columns/indexes and via arbitrary SQL. `csv`/`json`
are implemented via DuckDB, and `sqlite`/`postgres` via SQLAlchemy (see
`app/drivers.py`); `mysql` can be saved as a record but raises `NotImplementedError`
when queried.

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
.venv/bin/pyinstaller --noconfirm mydataviewer.spec
./dist/mydataviewer/mydataviewer      # the runnable output - see packaging gotchas below
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

`DatasourceRepository` has two distinct halves:
1. CRUD against the local `datasources` sqlite table (scoped by `profile_id`).
2. "Live" operations against the actual external source - `list_tables`,
   `list_columns`, `list_indexes`, `execute_sql` - which delegate through
   `drivers.get_driver(datasource)` to a per-type driver class (`app/drivers.py`).
   Only `CsvDriver` exists; it registers the CSV as a DuckDB view via the **relation
   API** (`con.read_csv(path).create_view(name)`), deliberately not by
   string-interpolating the file path into SQL (DuckDB also can't bind a `?`
   parameter inside `CREATE VIEW ... read_csv_auto(?)` - it raises `BinderException`,
   which is why the relation API is used instead). Adding postgres/mysql support
   means adding driver classes here and wiring them into `get_driver()`.

### Migrations (`app/db/migrations/*.sql`)

Schema changes are made by **adding a new numbered `.sql` file** (next sequence
number, e.g. `0005_...sql`) - never edit an already-applied migration.
`run_migrations()` applies any file not yet recorded in `schema_migrations`, in
filename order, once per file. `app/db/paths.py` resolves the migrations directory
via `sys._MEIPASS` when frozen so it works both from source and from a PyInstaller
build - **any new migration file must also be picked up by the existing
`datas=[('app/db/migrations', 'app/db/migrations')]` glob-style entry in
`mydataviewer.spec`** (it already covers the whole directory, so no spec change is
needed unless the directory itself moves).

### Version (`app/version.py`, `VERSION`)

`app/version.py::get_version()` reads the plain-text `VERSION` file at the
project root (via `app/db/paths.py::project_root()` - the same
frozen-vs-source resolution the migrations dir uses) and is what both the
Help > About menu item and the sidebar's About page display. It is **not**
derived from git (`git describe` etc.) - a PyInstaller build has no `.git`
directory to read that from at runtime, so `VERSION` is baked in at build
time instead. Anywhere this file needs to be read from, it must also be
**bundled alongside `main.py`/`app/`**:
- `mydataviewer.spec`'s `datas=[...]` includes `('VERSION', '.')`.
- `aur/PKGBUILD`'s `package()` copies `VERSION` next to `main.py` under
  `/usr/lib/mydataviewer/`, same as it does for `main.py` itself.

See the root `README.md`'s "Versioning" section and `AUR.md` for how
`VERSION` gets bumped (`scripts/bump-app-version.py`) and how it flows into
release tags, GitHub Actions artifact filenames, and the AUR package's
`pkgver`.

### UI structure

Left `Sidebar` (icon buttons, `app/sidebar.py`) drives a `wx.Simplebook` in
`MainFrame` - `SIDEBAR_ITEMS` order must match the order pages are added to the book
(`Sidebar._on_button_clicked` selects by index). Each screen is a
`<concept>_page.py` (list/filter/CRUD toolbar built on `wx.ListCtrl`) +
`<concept>_dialog.py` (modal `wx.Dialog` create/edit form) pair - see
`profiles_page.py`/`profiles_dialog.py` and `datasources_page.py`/
`datasources_dialog.py` for the pattern to follow when adding a new concept's screen.

### PyInstaller packaging gotchas (already bit us once - see git history)

- **Always build with this project's own venv's pyinstaller**
  (`.venv/bin/pyinstaller`), never a bare `pyinstaller` resolved from `PATH` - it can
  silently resolve to a *different* project's venv that lacks `duckdb`, producing a
  build that fails at runtime with `ModuleNotFoundError: No module named 'duckdb'`.
- **Build via `pyinstaller --noconfirm mydataviewer.spec`**, not
  `pyinstaller ... main.py` - the latter regenerates/overwrites `mydataviewer.spec`
  from scratch, silently wiping the `datas` entry that bundles the SQL migrations.
- **The runnable output is `dist/mydataviewer/mydataviewer`.** `build/` is
  PyInstaller's intermediate scratch directory and is never a complete, runnable
  tree - executing anything under it will fail to find shared libraries.
