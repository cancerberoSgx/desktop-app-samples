# My Redis Viewer

A desktop app, built with [wxPython](https://wxpython.org), for managing Redis
connections: organize them into profiles, and check that a connection is reachable
via a PING-based "Connect" check. There is no data-browsing/query UI - this app is
purely for managing and testing Redis connection details.

## Project layout

```
main.py                       Entry point
app/
  frame.py                    Main window: menu bar + sidebar + page-switching area
  sidebar.py                  Left navigation sidebar with icon buttons
  pages.py                    About page
  profiles_page.py            "Profiles" screen: list + CRUD toolbar + Activate
  profiles_dialog.py          Create/edit form for a single profile
  datasources_page.py         "Data Sources" screen: list + filter + CRUD + Connect
  datasources_dialog.py       Create/edit form for a single data source
  models.py                   Datasource / Profile dataclasses
  repositories.py             ProfileRepository / DatasourceRepository / SettingsRepository
                               (pure SQL against SQLite; DatasourceRepository.test_connection
                               opens a real connection via redis-py and PINGs it)
  db/
    paths.py                  Resolves ~/.my-redis-viewer and the migrations folder
    connection.py              SQLite connection factory
    migrator.py                 Applies any new *.sql file under db/migrations/
    migrations/
      0001_create_profiles.sql
      0002_create_datasources.sql
      0003_create_settings.sql
requirements.txt
myredisviewer.spec
```

## Data storage

App data (profiles and data sources) lives in a SQLite database at
`~/.my-redis-viewer/my-redis-viewer.db`, created on first run. Schema changes are
made by adding a new numbered `.sql` file under `app/db/migrations/` (e.g.
`0004_add_something.sql`) - `run_migrations()` applies any file not yet recorded
in the `schema_migrations` table, in filename order.

## Profiles

Everything (currently just data sources) belongs to a profile. On first run a
`"default"` profile is created automatically. Create/edit/delete/activate profiles
from the "Profiles" screen; activating one switches which profile's data sources
the "Data Sources" screen shows. Deleting a profile deletes its data sources too
(`ON DELETE CASCADE`).

## Data Sources

A data source is a Redis connection with:

- `name`
- `redis_host`
- `redis_port`
- `redis_user`
- `redis_password`

From the "Data Sources" screen you can create/edit/delete/list data sources
(filterable by name), and **Connect**, which opens a connection via
[redis-py](https://github.com/redis/redis-py) and issues a `PING` - a message box
reports success or the connection error. There is no further data exploration
(no key browsing, no command console) - Connect is purely a reachability check.

redis-py is a pure-Python client speaking the Redis wire protocol over a plain TCP
socket, so it works unmodified on Linux, Windows, and macOS.

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

## Run test (development)

See test/README.md

## Installing on Arch Linux (AUR)

There's an `aur/PKGBUILD` in this directory that installs `myredisviewer` as
a normal, dynamically-linked Arch package (`python`, `python-wxpython`,
`python-redis` from the official repos, nothing bundled) - much lighter than
the PyInstaller build below, since it shares the system's already-installed
Python/wxWidgets instead of vendoring a private copy of each. See `/AUR.md`
at the repo root for how to test it locally and publish it to AUR.

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
pyinstaller --noconfirm myredisviewer.spec
```

The spec file bundles `app/db/migrations/*.sql` as data files so the app can run
its migrations from a packaged build too - if you add new modules that read
other files off disk at runtime, add them to `datas` in `myredisviewer.spec` the
same way.

The executable is created at `dist/myredisviewer/myredisviewer` (`.exe` on
Windows, `dist/myredisviewer.app` plus `dist/myredisviewer/` on macOS). Distribute
the whole `dist/myredisviewer/` folder, not just the executable - it depends on
the other files placed alongside it (GTK must be present on the target Linux
machine; on macOS, an unsigned app must be right-clicked > Open to bypass
Gatekeeper, or signed/notarized for distribution).
