# My Data Viewer

A desktop app, built with [wxPython](https://wxpython.org), for exploring databases
and CSV files: define data sources, then browse their tables/columns/indexes and
run SQL against them.

## Project layout

```
main.py                       Entry point
app/
  frame.py                    Main window: menu bar + sidebar + page-switching area
  sidebar.py                  Left navigation sidebar with icon buttons
  pages.py                    Home / About pages
  datasources_page.py         "Datasources" screen: list + filter + CRUD toolbar
  datasources_dialog.py       Create/edit form for a single datasource
  models.py                   Datasource / ColumnInfo / IndexInfo / QueryResult
  repositories.py             DatasourceRepository (CRUD + list_tables/list_columns/
                               list_indexes/execute_sql, pure SQL against SQLite)
  drivers.py                  Per-type drivers used by the repository to run
                               operations against the actual data source (only
                               "csv", via DuckDB, is implemented so far)
  db/
    paths.py                  Resolves ~/.my-data-viewer and the migrations folder
    connection.py             SQLite connection factory
    migrator.py                Applies any new *.sql file under db/migrations/
    migrations/
      0001_create_datasources.sql
requirements.txt
mydataviewer.spec
```

## Data storage

App data (currently just the `datasources` table) lives in a SQLite database at
`~/.my-data-viewer/my-data-viewer.db`, created on first run. Schema changes are
made by adding a new numbered `.sql` file under `app/db/migrations/` (e.g.
`0002_add_something.sql`) - `run_migrations()` applies any file not yet recorded
in the `schema_migrations` table, in filename order.

## Datasources

A datasource has a `type` of `postgres`, `mysql` or `csv`. Only `csv` is wired up
end-to-end right now:

- **Create/edit/delete/list** datasources from the "Datasources" screen (list can
  be filtered by name-contains and by type).
- For a `csv` datasource, `file_path` is required. `DatasourceRepository` exposes
  `list_tables`, `list_columns`, `list_indexes` and `execute_sql`, delegating to
  `CsvDriver` (in `drivers.py`), which registers the CSV as a table in an
  in-memory [DuckDB](https://duckdb.org) connection (via `read_csv_auto`) so SQL
  can be run against it without loading the file into Python:
  - `list_tables()` returns a single table named after the CSV file.
  - `list_columns()` returns the CSV's column names, all typed `"text"`.
  - `execute_sql(sql)` runs arbitrary SQL (e.g. `SELECT * FROM <table> ORDER BY
    ... WHERE ...`) and returns `(columns, rows)`.
- `postgres`/`mysql` can be saved as a datasource already (the schema and CRUD
  support all their fields), but their driver operations raise
  `NotImplementedError` until those drivers are built.

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
