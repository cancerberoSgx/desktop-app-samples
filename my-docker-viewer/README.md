# My Docker Viewer

A desktop app, built with [wxPython](https://wxpython.org), for admin'ing local Docker
containers: see every container's status, image, size, and live CPU/memory usage in
one list, filter it down, and stop or remove containers - all through the `docker`
CLI, with no Docker SDK/Engine API dependency.

## Project layout

```
main.py                       Entry point
app/
  frame.py                    Main window: menu bar + sidebar + page-switching area
  sidebar.py                  Left navigation sidebar with icon buttons
  pages.py                    About page
  containers_page.py          "Containers" screen: list + filter + stop/remove + auto-refresh
  models.py                   Container dataclass
  repositories.py             ContainerRepository (wraps the docker CLI) / SettingsRepository
  async_task.py               AsyncTaskRunner - runs docker CLI calls off the UI thread
  db/
    paths.py                  Resolves ~/.my-docker-viewer and the migrations folder
    connection.py              SQLite connection factory
    migrator.py                 Applies any new *.sql file under db/migrations/
    migrations/
      0001_create_settings.sql
requirements.txt
mydockerviewer.spec
```

## Requirements

- Docker installed and the `docker` CLI available on `PATH` - this app has no
  bundled Docker runtime and no Docker SDK dependency; every operation shells out
  to `docker` itself. If `docker` can't be found, the Containers screen shows a
  clear error instead of a container list.

## Containers

The "Containers" screen lists every container, running and stopped
(`docker ps -a`), with:

- Name, image, status, and created date
- Size on disk (`docker ps --size`)
- Live CPU % and memory usage/percent (`docker stats --no-stream`) for running
  containers - stopped containers show `-` since Docker has no resource stats for
  a container that isn't running

The list refreshes automatically every few seconds so CPU/memory stay current, and
can be refreshed on demand with the **Refresh** button. Filter by name, image, or
status (matching Docker's own container states: running, exited, paused,
restarting, created, removing, dead) using the toolbar controls above the list -
filtering is instant and doesn't re-query Docker.

Selecting a container enables:

- **Stop** - `docker stop` (only enabled while the container is running)
- **Remove** - `docker rm`; removing a running container asks for confirmation and
  force-removes it (`docker rm -f`)

## Data storage

App settings live in a SQLite database at `~/.my-docker-viewer/my-docker-viewer.db`,
created on first run, with schema changes applied from `.sql` files under
`app/db/migrations/`. Nothing is stored there yet - it's wired up ahead of time so a
future preference (remembered filters, refresh interval, ...) has a ready-made place
to live.

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
pyinstaller --noconfirm mydockerviewer.spec
```

The spec file bundles `app/db/migrations/*.sql` as data files so the app can run
its migrations from a packaged build too - if you add new modules that read
other files off disk at runtime, add them to `datas` in `mydockerviewer.spec` the
same way.

The executable is created at `dist/mydockerviewer/mydockerviewer` (`.exe` on
Windows, `dist/mydockerviewer.app` plus `dist/mydockerviewer/` on macOS). Distribute
the whole `dist/mydockerviewer/` folder, not just the executable - it depends on
the other files placed alongside it (GTK must be present on the target Linux
machine; on macOS, an unsigned app must be right-clicked > Open to bypass
Gatekeeper, or signed/notarized for distribution). The machine running the built
executable also needs Docker itself installed and on `PATH`.
