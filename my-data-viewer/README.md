# My Data Viewer

A desktop app, built with [wxPython](https://wxpython.org), for exploring databases
and CSV/JSON files: pick a profile, define its data sources, then browse their
tables/columns/indexes, run SQL against them, save reusable scripts, and export the
results to Parquet.

## Project layout

```
main.py                       Entry point
app/
  frame.py                    Main window: menu bar + sidebar + page-switching area,
                               app-wide drag-and-drop, exit-time unsaved-scripts check
  sidebar.py                  Left navigation sidebar with icon buttons
  pages.py                    About page
  profiles_page.py            "Profiles" screen: list + CRUD toolbar
  profiles_dialog.py          Create/edit form for a single profile
  datasources_page.py         "Datasources" screen: list + filter + CRUD toolbar,
                               scoped to the active profile
  datasources_dialog.py       Create/edit form for a single datasource (also used
                               to prefill a new datasource from a dropped file)
  data_explore_page.py        "Explore" screen for a connected datasource - Tables
                               (Fields/Data/Indexes), Scripts and Actions tabs
  models.py                   Datasource / DatasourceField / Script / ColumnInfo /
                               IndexInfo / QueryResult
  repositories.py             ProfileRepository, DatasourceRepository (CRUD +
                               list_tables/list_columns/list_indexes/execute_sql/
                               export_to_parquet/export_schema_to_parquet),
                               ScriptRepository, SettingsRepository - pure SQL
                               against SQLite
  drivers.py                  Per-type drivers used by the repository to run
                               operations against the actual data source (csv,
                               json and postgres are implemented; mysql is not)
  db/
    paths.py                  Resolves ~/.my-data-viewer and the migrations folder
    connection.py             SQLite connection factory
    migrator.py                Applies any new *.sql file under db/migrations/
    migrations/                0001..0008, adding profiles, datasource fields,
                               scripts and per-datasource "last opened script"
requirements.txt
mydataviewer.spec
```

## Data storage

App data (profiles, datasources, their inferred/declared fields, saved scripts, and
app settings) lives in a SQLite database at `~/.my-data-viewer/my-data-viewer.db`,
created on first run. Schema changes are made by adding a new numbered `.sql` file
under `app/db/migrations/` (e.g. `0009_add_something.sql`) - `run_migrations()`
applies any file not yet recorded in the `schema_migrations` table, in filename
order.

## Profiles

Everything else in the app - currently, datasources - belongs to a profile. A
"default" profile is created automatically the first time the app runs (and again
if the last remaining profile is ever deleted), so there's always at least one to
work in. Switching the active profile on the Profiles screen reloads the
Datasources screen to show only that profile's data sources.

## Datasources

A datasource has a `type` of `postgres`, `mysql`, `csv` or `json`:

- **Create/edit/delete/list** datasources from the "Datasources" screen (list can
  be filtered by name-contains and by type).
- `csv` and `json` datasources point at a local `file_path` and are queried via
  [DuckDB](https://duckdb.org) (`CsvDriver`/`JsonDriver` in `drivers.py`), which
  registers the file as a view through DuckDB's relation API rather than
  string-interpolating the path into SQL. Column types can be inferred
  automatically ("Infer types" in the datasource dialog) and are then persisted
  per-datasource so they don't need to be re-detected on every load.
- `postgres` datasources connect over SQLAlchemy (`PostgresDriver`) and support the
  same `list_tables`/`list_columns`/`list_indexes`/`execute_sql` operations as the
  file-based types.
- `mysql` can be saved as a datasource already (the schema and CRUD support all its
  fields), but its driver raises `NotImplementedError` until that driver is built.

### Opening a file via drag-and-drop

Dropping a `.csv`, `.json`, `.ndjson` or `.jsonl` file anywhere in the app window:

- opens it directly if a datasource in the active profile already points at that
  file path, or
- switches to the Datasources screen and opens "New Datasource" prefilled with the
  dropped file's path, type and a name defaulted from the filename, ready to save.

## Exploring a datasource

"Connect" on the Datasources screen opens the Explore screen for that datasource,
with three tabs:

- **Tables** - lists the datasource's tables on the left; the selected table's
  Fields, Data and Indexes show on the right. The Data grid is an Excel-like
  `wx.grid.Grid` (sorting/filtering/pagination are all pushed down into SQL, so
  only one bounded page of rows is ever loaded into memory): click a cell to
  select it, ctrl-click to add cells, shift-click to extend a block, click a
  column's handle glyph or a row number to select the whole column/row, and
  Ctrl+C or the right-click menu copies the selection as tab/newline-separated
  text.
- **Scripts** - save, rename, delete and edit named SQL scripts per datasource, run
  the whole script or just the selected statement, and browse results in the same
  kind of grid. A first script is seeded automatically the first time a datasource
  is opened. Edits are kept in memory until explicitly saved; the datasource
  remembers which script was last open and reselects it next time. Closing the app
  with any script's edits unsaved across any datasource prompts to save all,
  discard all, or cancel and jump back to the first unsaved one.
- **Actions** - exports the datasource, regardless of type:
  - **Export as Parquet...** writes every table to its own `<table>.parquet` file
    in a chosen folder.
  - **Export schema as Parquet...** writes one row per column (table, column,
    type, constraints) across every table into a single Parquet file.

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

Install PyInstaller into the same virtual environment used to run the app:

```bash
pip install pyinstaller
```

```bash
pyinstaller --noconfirm --windowed --name mydataviewer main.py


# pyinstaller --noconfirm mydataviewer.spec
```

The spec file bundles `app/db/migrations/*.sql` as data files so the app can run
its migrations from a packaged build too - if you add new modules that read
other files off disk at runtime, add them to `datas` in `mydataviewer.spec` the
same way.

The executable is created at `dist/mydataviewer/mydataviewer` (`.exe` on
Windows, `dist/mydataviewer.app` plus `dist/mydataviewer/` on macOS). Distribute
the whole `dist/mydataviewer/` folder, not just the executable - it depends on
the other files placed alongside it (GTK must be present on the target Linux
machine; on macOS, an unsigned app must be right-clicked > Open to bypass
Gatekeeper, or signed/notarized for distribution).

### Packaging notes for DuckDB

- DuckDB ships a large compiled native library; PyInstaller usually detects it
  automatically, but if the built executable fails to import `duckdb`, add its
  dynamic libraries explicitly (e.g. via `collect_dynamic_libs("duckdb")` in the
  spec's `Analysis(...)` binaries).
- Some DuckDB features (e.g. certain extensions) can be downloaded on first use.
  If the packaged app needs to run fully offline, avoid relying on those or
  bundle the required extensions ahead of time.
