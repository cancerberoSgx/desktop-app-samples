# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A wxPython desktop app for admin'ing local Docker containers: list every container
(running and stopped) with its status, image, size, and live CPU/memory usage,
filter the list by name/image/status, and stop or remove the selected one. A second,
read-only screen (Containers Disk) shows real per-container disk usage - writable
layer plus every volume/bind mount - to answer "what's actually eating my disk".
Three more screens - Images, Volumes, Networks - each list one other docker
resource type with its size/usage and how many containers (running or stopped)
reference it, and can remove one or prune every unused one at once; removing an
image that's still in use additionally offers to cascade to its containers/
volumes/networks too, to reclaim the most space in one step.

This project was templated from the sibling `my-redis-viewer` app for its overall
architecture (composition root in `frame.py`, sidebar + `wx.Simplebook`, `AsyncTaskRunner`
facade, SQLite + migrations) - but **no feature was copied**: there are no profiles, no
datasources, no data-explorer concept. All five screens (Containers, Containers Disk,
Images, Volumes, Networks) are new concepts built from scratch on top of the
docker CLI.

**There is no docker SDK/Engine API dependency.** Every docker operation - listing,
stats, stop, remove - shells out to the `docker` binary and parses its
`--format '{{json .}}'` output (`app/repositories.py::ContainerRepository`,
`ImageRepository`, `VolumeRepository`, `NetworkRepository`). This was an explicit
choice: it needs nothing beyond a working Docker install already on PATH, matches
what `docker` itself reports, and avoids pinning to a specific docker-py version/API
compatibility matrix.

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
constructs a plain `ContainerRepository()`, `DiskUsageRepository()`,
`ImageRepository()`, `VolumeRepository()`, and `NetworkRepository()` (no
connection/state - all five are stateless CLI wrappers). There is no dependency
injection framework - everything is wired by hand in this one place, same as
`my-redis-viewer`.

### `ContainerRepository` (`app/repositories.py`) - the docker CLI wrapper

- `list_identity()` runs `docker ps -a --size --format '{{json .}}'` (one JSON
  object per line - identity, status, size); `stats()` separately runs `docker
  stats --no-stream --format '{{json .}}'` and returns live CPU/memory keyed by
  container ID - `docker stats` only reports running containers, so stopped
  ones are simply absent from that dict. These are two separate methods, not
  one merged call, because `docker ps` is near-instant while `docker stats
  --no-stream` has to wait out a full sampling window - `ContainersPage.reload()`
  runs both concurrently and renders identity the moment it lands rather than
  blocking the table on the slower of the two (see below). `list()` still
  exists as a convenience that runs both sequentially and merges them into one
  snapshot, for any caller that wants that and doesn't care about latency.
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
  it for `_on_start()`, `_on_stop()`, and `_on_remove()`. `reload()` itself does
  **not** use it - see "Containers list: identity and stats load independently"
  below for why - it uses the lower-level `run_background()` instead, same as
  `ContainersDiskPage`'s per-container jobs.
- `AsyncTaskRunner.run()` ignores a second call while one is already in flight on
  that instance - this is what keeps a stray double-click (or an auto-refresh
  timer tick landing mid-action) from stacking overlapping `docker
  start`/`stop`/`rm` calls.
- Built on `wx.lib.delayedresult.startWorker`, not `asyncio` - keep using it (or
  `run_background()` for independent concurrent jobs) for any new blocking
  docker call rather than introducing a second concurrency model.

### Containers list: identity and stats load independently

`ContainersPage.reload()` fires two concurrent background jobs via
`run_background()` (not `AsyncTaskRunner`, which only runs one task at a time)
- `ContainerRepository.list_identity()` (`docker ps`) and `.stats()` (`docker
stats --no-stream`) - instead of one sequential call, because `docker stats`
has to wait out a full sampling window and would otherwise hold back the
already-available identity data for that whole time, leaving the table
sitting blank/stale for no reason. Whichever job finishes first renders
first: identity landing populates every column except cpu/mem (shown as `-`
until stats arrives); stats landing merges cpu/mem into whatever's already on
screen by container ID. `_reload_pending` (an int, not a bool - two jobs) is
this cycle's busy flag: `reload()` bails if it's nonzero (so the auto-refresh
timer can't stack a new cycle on top of one still in flight), and
`_on_start`/`_on_stop`/`_on_remove` also bail on it, since `AsyncTaskRunner`'s
own busy flag no longer covers reload the way it used to when reload ran
through that same runner. A job can land in either order - if stats arrives
before identity (identity is normally the faster of the two, but isn't
guaranteed to be), it's held in `_pending_stats` and merged in once identity
shows up (`_identity_ready`), rather than merged against the previous cycle's
containers or dropped. A `docker stats` failure alone doesn't blank the
screen the way an identity failure does - `docker ps` already succeeded (or
will) independently, so cpu/mem just stay `-` for that cycle.

### Auto-refresh

`ContainersPage` runs a `wx.Timer` (`AUTO_REFRESH_INTERVAL_MS`, currently 5s) that
calls `reload()` on every tick, since CPU/memory are point-in-time samples and go
stale immediately after a manual refresh. The timer is **opt-in and off by
default** - it's only started/stopped via the "Auto-refresh" checkbox
(`_on_auto_refresh_toggle`), so the app doesn't hit the docker CLI on a
recurring basis unless the user asks for it; otherwise the user drives updates
with the Refresh button. `reload()` re-renders the list from the freshly-fetched
data while preserving the current selection by container ID (see
`_populate_list`), so an in-flight auto-refresh doesn't fight with the user
selecting a row. The timer is stopped on `EVT_WINDOW_DESTROY` so it can't fire
against a torn-down page.

### Stop/remove: optimistic UI updates

`_on_stop`/`_on_remove` don't call `reload()` on success - re-fetching via
`docker ps`/`docker stats` after every action would mean the table stays stale
for another full round trip right after the user just acted. Instead
`_apply_stopped()`/`_apply_removed()` mutate the already-loaded
`self._containers` in place (mark state `exited` and clear the cpu/mem fields,
or drop the entry entirely) and call `_populate_list()` directly - the visible
change is instantaneous. This is optimistic: it reflects what the user's
`docker stop`/`docker rm` call is known to have just done, not a fresh read of
docker's own state - the next manual refresh or auto-refresh tick reconciles
it with docker's actual reporting (exact `Status` text, final size, etc.).

### Filtering

Name/image/status filters (`_name_filter`, `_image_filter`, `_status_choice` in
`ContainersPage`) are applied **client-side** against the last `list()` result
(`_filtered_containers()`) - no docker call is re-issued on every keystroke, only
`reload()` (manual refresh or the timer) hits the CLI. Status filtering matches
against docker's own `State` field (`running`, `exited`, `paused`, `restarting`,
`created`, `removing`, `dead`), not the human-readable `Status` string.

### `DiskUsageRepository` (`app/repositories.py`) - real per-container disk usage

Backs the read-only "Containers Disk" screen (`app/containers_disk_page.py`),
answering "which container is using the most disk space" - `docker ps --size`
(the `Size` column on the main Containers screen) only covers a container's own
writable layer, not its volumes/bind mounts, which is usually where the real
space goes (a database's data directory, for instance).

- `list_targets()` is cheap (no `du`, no `--size`) and loads automatically like
  the main screen: identity via `docker ps`, mount composition via one bulk
  `docker inspect --format '{{json .Mounts}}'` call. Cross-referencing mounts
  across every container marks a volume/bind path `shared` when more than one
  container uses it - freeing space by removing just one of them won't reclaim
  a shared mount, which matters directly for "what can I delete".
- Actual sizing (`ensure_helper_image`, `container_layer_bytes`,
  `mount_usage_bytes`/`sum_mounts_bytes`) only ever runs via **Calculate**
  (`ContainersDiskPage._on_calculate`) - never on a timer or just from loading
  the page - because both halves are comparably expensive to the main
  screen's `docker stats` wait: `docker inspect --size` is a filesystem diff
  (measured ~0.3-0.5s per container), and `du` is a full tree walk. Calculate
  runs once automatically the first time (and only the first time) the user
  actually navigates to this page (`on_shown()`, called from
  `MainFrame._on_sidebar_select` - not on construction, since every page is
  built eagerly at startup) so it isn't a wall of "Not calculated" requiring
  a click before showing anything; every visit after that, including after a
  Refresh, is left to the user via the button.
- **A mount's size is measured by running a disposable helper container**
  (`docker run --rm -v <mount>:/mnt/target:ro alpine du -sk /mnt/target`)
  rather than reading the host path directly - measured, not assumed, before
  choosing this: named volumes are root-owned on Linux and unreadable by an
  ordinary user even when `docker` itself works fine for them, and under
  Docker Desktop (macOS/Windows) volumes live inside its VM and are never
  visible to the host filesystem at all. Routing through the daemon sidesteps
  both, identically on Linux/macOS/Windows - there is no OS-specific branch
  anywhere in this repository. `du -sk` (kilobytes) is used rather than a
  "bytes" flag because BusyBox's `-b` means "apparent size", not "output unit:
  bytes" - `-s`/`-k` alone behave the same on BusyBox and GNU du.
- **Never risk creating docker resources from what is meant to be a read-only
  screen.** Referencing a volume name via `docker run -v <name>:...` silently
  *creates* an empty volume with that name if it's gone missing - so a removed
  volume is filtered out at `list_targets()` time (noted, not sized), and
  `mount_usage_bytes` re-checks with `docker volume inspect` immediately
  before running `du` too, since Calculate can run long after that snapshot
  (or something else on the machine can remove the volume mid-calculation). A
  missing bind-mount source is checked the cheap way, with `os.path.exists`.
- Every container is sized by its **own independent job**
  (`ContainersDiskPage._start_container_job`) - its own layer size and its own
  mounts total both happen inside that one job - so one container's row is
  never held up by another's pace; whichever finishes first renders first,
  which is the whole point of the "Calculating... (n/total)" streaming UI.
  Earlier this fetched every container's layer size in one shared bulk call
  up front; that was measured to cost about the same *per container* as
  fetching it individually, so batching bought nothing but forced every row
  to wait on the slowest shared call.
- `MAX_CONCURRENT_DU_RUNS` bounds how many `du` helper containers run at once
  across an entire Calculate pass, regardless of container/mount count.
- A mount that can't be sized (unsupported type, or vanished per the safety
  checks above) is excluded from that container's total and noted rather than
  failing the whole row - `sum_mounts_bytes` returns `(total_bytes, notes)`
  instead of raising, so one bad mount doesn't blank out an otherwise-good
  number.
- Both `ContainerRepository` and `DiskUsageRepository` shell out through the
  shared module-level `_run_docker()` (extracted so `DiskUsageRepository`
  could pass its own longer timeouts for `du`/image-pull without duplicating
  the `DockerNotAvailableError`/`DockerCommandError` handling).
- `ContainersDiskPage` doesn't use `AsyncTaskRunner` for Calculate - that
  facade is deliberately single-flight (`is_busy()` ignores a second call),
  which doesn't fit "N independent per-container jobs streaming back
  concurrently". It uses `run_background()` (`app/async_task.py`) instead -
  the same `delayedresult.startWorker` + destroyed-window-safety plumbing
  `AsyncTaskRunner.run()` is built on, minus the single-flight/disable/
  `on_done` bookkeeping that only makes sense for one task bound to specific
  widgets. `reload()` (identity + mounts) still uses `AsyncTaskRunner`, same
  as the main screen.

### `ImageRepository` (`app/repositories.py`) - the Images screen's docker CLI wrapper

Backs `app/images_page.py`. Same shell-out-and-parse-`{{json .}}` pattern and
`_run_docker()`/`DockerNotAvailableError`/`DockerCommandError` handling as
`ContainerRepository` - no separate error model was introduced for images.

- `list()` runs `docker image ls --format '{{json .}}'` - **deliberately without
  `-a`**: that flag also surfaces intermediate build-cache layers, which aren't
  images a user would ever remove/prune individually; the no-`-a` list is what
  `docker images` shows by default, and what this screen shows too.
- Container count comes for free: docker's own `Containers` format placeholder on
  `image ls` already reports how many containers (running or stopped) reference
  each image, **no cross-referencing against `docker ps` needed** - confirmed by
  running it directly rather than assumed. `Image.status` (`"In use"` / `"Unused"`
  / `"Dangling"`) is a client-side classification derived from that count plus
  `Repository`/`Tag` being `<none>`, used for both the Status column and the
  status filter - docker itself doesn't report a single "status" field for images.
- `remove(reference, force=...)` / `prune(all_unused=...)` shell out to `docker
  image rm [-f]` / `docker image prune -f [-a]` directly - same "no dry-run, no
  undo" posture as `ContainerRepository.stop`/`remove`. `prune()` returns docker's
  own stdout (each deleted image, then "Total reclaimed space: ...") verbatim so
  `ImagesPage` can show the user exactly what happened rather than re-deriving it.
- `ImagesPage._on_remove`: an image with zero referencing containers goes straight
  to a plain yes/no confirm. One with at least one referencing container instead
  triggers `find_dependents()` (async) and then `_RemoveImageDialog` - see below -
  rather than the old unconditional `force=True` remove; either path's "remove
  image only" branch still passes `force=True` and can still fail behind a
  *running* container even so, which surfaces as a normal `on_error` message
  rather than something the dialog pre-empts.
- **Cascading remove** (`find_dependents()` / `remove_with_dependents()` /
  `_RemoveImageDialog` in `images_page.py`) - lets the user remove an image
  *and* every container/volume/network that only exists because of it, to
  reclaim the most space in one step, rather than removing the image and
  leaving its now-pointless containers/volumes/networks behind:
  - `find_dependents(reference)` is read-only and answers "what would a cascade
    take out": every container built from this exact image (any state), plus the
    volumes/networks those containers use. `docker ps --filter
    ancestor=<reference>` is the obvious way to find candidate containers, but
    its own docs say it also matches containers running a *descendant* of this
    image (something built `FROM` it) - not "uses this image". So candidates are
    cross-checked against each container's own `.Image` (exact image ID via one
    bulk `docker inspect`) rather than trusted outright - measured, not assumed,
    same posture as `DiskUsageRepository`'s mount-safety checks.
  - A dependent volume/network is marked `shared` (and skipped by the cascade,
    not removed) if `docker ps -a --filter volume=.../network=...` shows some
    container *outside* the set being removed still references it - checked with
    `-a` deliberately, since a stopped container still needs its volume/network
    back the next time it starts. Predefined networks (`bridge`/`host`/`none`)
    are excluded from cascade candidates outright - docker never lets you remove
    them regardless.
  - `remove_with_dependents()` removes every dependent container (`docker rm
    -f`), then every non-shared dependent volume/network, then the image itself
    - continuing past an individual step's failure rather than aborting the
    whole cascade over one bad item (same posture as
    `DiskUsageRepository.sum_mounts_bytes`), returning one human-readable note
    per step for the result dialog.
  - `_RemoveImageDialog` defaults to "remove image only" (the less destructive
    choice) and only reveals the containers/volumes/networks detail list once
    the user picks "remove image and all associated resources" - kept vs. would-
    be-removed items are called out separately so a shared volume/network being
    *kept* isn't mistaken for an oversight.
  - A successful cascade also calls `on_containers_changed` (wired in
    `frame.py` to `ContainersPage.reload`) since it can delete containers
    `ImagesPage` never loaded itself - without this hook the Containers screen
    would sit stale showing containers that no longer exist until the user
    happened to revisit it.
- Prune is the one action on this page that calls `reload()` on success instead of
  patching `self._images` in place - the set of images a prune deletes is docker's
  own unused/dangling determination, not something worth re-deriving client-side.
  That `reload()` call goes through `wx.CallAfter` because `AsyncTaskRunner` is
  single-flight and hasn't cleared its busy flag yet inside the same success
  callback - calling `reload()` synchronously there would be silently ignored by
  `is_busy()`.
- **No auto-refresh timer on this page**, unlike `ContainersPage` - an image list
  only changes when something actually adds/removes an image (this app, another
  docker client, a build), not every few seconds like CPU/mem, so a manual Refresh
  is enough and this page never hits the docker CLI on its own.
- `app/formatting.py::size_sort_key` (parses a docker size string to bytes for
  numeric column sorting) was extracted out of `containers_page.py` once
  `images_page.py` needed the same logic for its Size column - both pages import
  it from there now rather than each keeping their own copy.

### `VolumeRepository` / `NetworkRepository` (`app/repositories.py`) - Volumes and Networks screens

Back `app/volumes_page.py` / `app/networks_page.py`, structured as close to
`ImagesPage`/`ImageRepository` as the two resource types allow: same shell-out-
and-parse-`{{json .}}` pattern, same status filter/Remove/Prune toolbar shape,
same "no auto-refresh timer" reasoning (a volume/network list only changes when
something actually creates/removes one).

- Neither `docker volume ls` nor `docker network ls` reports which containers
  use a given volume/network, unlike images (`docker image ls`'s own
  `Containers` field) - so both repositories' `list()` cross-reference against
  every container themselves, but via two *different* docker fields, because
  the data is exposed differently for each:
  - `VolumeRepository` bulk-inspects every container's `Mounts`
    (`docker inspect --format '{{json .Mounts}}'`) - the same shape
    `DiskUsageRepository.list_targets` uses, kept as its own independent,
    smaller implementation here (only cares about `Type == "volume"` mounts,
    not the bind-mount/tmpfs handling that read-only screen also carries)
    rather than reusing that class's internals across an unrelated screen.
  - `NetworkRepository` needs no extra `docker inspect` call at all - every
    container's own `docker ps` row already reports a comma-separated
    `Networks` field, cheaper than a second bulk call.
- **Volumes' Size column is computed on demand, not read off `docker volume
  ls`** - see the dedicated section below.
- **Neither volumes nor networks have a `force` override for "in use".**
  `docker volume rm -f` / `docker network rm -f` only suppress a "no such
  volume/network" error - they do not override docker's refusal to remove
  something still referenced by a container, unlike `docker rm -f` for
  containers or `docker image rm -f`. So `VolumesPage._on_remove` /
  `NetworksPage._on_remove` check `is_in_use` themselves and show an
  explanation naming the referencing containers *instead of* attempting a call
  that's guaranteed to fail - "explain before it fails" rather than
  round-tripping to docker's own less specific error.
- **`Network.is_builtin`** (`bridge`/`host`/`none` - the networks docker
  creates itself at daemon startup) disables the Remove button outright in
  `NetworksPage._update_button_states`, same reasoning as the in-use check
  above: docker never lets you remove these regardless of flags, so the
  button doesn't invite a click that can only fail.
- **Prune's `-a`/`--all` flag means something different per resource**, so
  each page's prune UI matches: `VolumesPage` keeps Images' "include
  <unused-but-still-named/tagged> resources" checkbox
  (`docker volume prune -a` extends beyond anonymous volumes to named ones);
  `NetworksPage` has no such checkbox at all - `docker network prune` has no
  `-a` flag, there's no dangling/anonymous-vs-everything distinction for
  networks to begin with.

### Volumes' Size column - reuses Containers Disk's `du`-helper-container approach

`docker volume ls` always reports size as `"N/A"` - real numbers need `docker
system df -v`, which is text-only (no `--format` support for the per-volume
breakdown) and comparably expensive to `DiskUsageRepository`'s
`du`-helper-container approach anyway. So rather than bolt on something
cheap-but-wrong, `VolumesPage` reuses that exact approach instead of
reimplementing sizing in `VolumeRepository`:

- `DiskUsageRepository.volume_usage_bytes(name)` is a thin wrapper around the
  same `mount_usage_bytes()` the Containers Disk screen uses for a
  container's volume mounts - it builds a synthetic `Mount(kind="volume",
  identifier=name, destination="")` (a volume has no `destination`; that's a
  per-*container* concept - where that container happens to mount it) and
  gets the same measured, safety-checked `du`-via-disposable-helper-container
  path for free, including its just-in-time `docker volume inspect` re-check
  immediately before running `du` (so a volume removed mid-Calculate doesn't
  get silently recreated empty by `docker run -v`).
- **`VolumesPage` is constructed with both `VolumeRepository` *and*
  `DiskUsageRepository`** (`frame.py` passes the same `DiskUsageRepository`
  instance used by `ContainersDiskPage`) - the only page in this app wired to
  two repositories, because sizing a volume and listing/removing/pruning
  volumes are genuinely different concerns backed by different docker
  commands.
- **Sharing that one `DiskUsageRepository` instance also shares its
  `MAX_CONCURRENT_DU_RUNS` semaphore** across both screens - running
  Calculate on Containers Disk and Volumes at the same time still caps the
  total number of simultaneous `du` helper containers, rather than each
  screen getting its own independent cap that could double the load.
- **Calculate is manual, never automatic, on this page** - unlike
  `ContainersDiskPage`, which auto-runs Calculate once on first visit
  (`on_shown`). Deliberately different: Containers Disk's job count is
  bounded by how many containers you have, while a machine can easily have
  dozens of volumes (95 on the machine this was built against) that have
  nothing to do with any container the user is currently looking at -
  silently kicking off that many disposable containers just because the user
  opened the page would be a surprise this page's other columns don't
  otherwise justify. Refresh still resets every row back to "Not calculated"
  (fresh `Volume` objects from `VolumeRepository.list()` carry no size
  state), same as Containers Disk's Refresh.
- **Calculate sizes every *loaded* volume, not just the currently filtered/
  visible ones** - so changing a filter mid-Calculate can't leave some rows
  permanently stuck at "Not calculated", and the "Calculating... (n/total)"
  progress readout stays honest against the real total.
- Refresh/Remove/Prune and Calculate are mutually exclusive on this page -
  `_update_button_states` disables Refresh/Remove/Prune while
  `self._calculating` is true, and the reverse (`reload()`/`_on_remove`/
  `_on_prune` all pass `self._calculate_btn` into their `AsyncTaskRunner`
  `disable=[...]` list) - because a Calculate job holds a direct reference to
  the very `Volume` objects a concurrent Refresh would replace wholesale.
- Same streaming-per-item shape as `ContainersDiskPage._start_container_job`:
  each volume is sized by its own independent `run_background` job (not
  `AsyncTaskRunner`, which is single-flight) so the fastest volumes render
  first instead of every row waiting on the slowest.
- `app/formatting.py::format_bytes` (raw-byte-count → human string, e.g.
  `1690624000` → `"1.7 GB"`) was extracted out of `containers_disk_page.py`
  once `volumes_page.py` needed the same formatting for its own Size column -
  both pages import it from there now, alongside the already-shared
  `size_sort_key` (which parses the *other* direction: a docker-reported size
  *string* into bytes, not a byte count we already hold into a string).

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
book (`Sidebar._on_button_clicked` selects by index): Containers (0),
Containers Disk (1), Images (2), Volumes (3), Networks (4). There is no About
entry in the sidebar (or an Exit button pinned under it, as there once was) -
"About" only exists as the Help menu's About dialog (`MainFrame._on_about`),
which is also the one place author/license/home links are shown.

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
