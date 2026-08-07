# AUR packaging

Each of the four wxPython apps in this repo has an `aur/` directory with a
PKGBUILD that installs it as a normal Arch Linux package - dynamically linked
against the distro's own `python`/`python-wxpython`/etc, nothing bundled. This
is deliberately a different distribution path from the PyInstaller builds in
`.github/workflows/*.yml`: those target machines with no package manager
guarantee (generic Linux, Windows, macOS) and so vendor a private copy of the
interpreter and every shared library; on Arch, pacman already guarantees
those are present, so bundling them would only add size and duplicate memory
for no benefit. See each app's `CLAUDE.md`/README for how the PyInstaller
path works; this file covers the AUR path only.

| App | `aur/` PKGBUILD | AUR pkgname | Binary | Extra runtime deps |
|---|---|---|---|---|
| my-data-viewer | `my-data-viewer/aur/PKGBUILD` | `my-data-viewer-git` | `mydataviewer` | `python-duckdb`, `python-sqlalchemy`; optional `python-psycopg2` for Postgres data sources |
| my-disk-viewer | `my-disk-viewer/aur/PKGBUILD` | `my-disk-viewer-git` | `mydiskviewer` | - |
| my-docker-viewer | `my-docker-viewer/aur/PKGBUILD` | `my-docker-viewer-git` | `mydockerviewer` | optional `docker` (the CLI it shells out to) |
| my-redis-viewer | `my-redis-viewer/aur/PKGBUILD` | `my-redis-viewer-git` | `myredisviewer` | `python-redis` |

All four of these extra deps (`python-duckdb`, `python-sqlalchemy`,
`python-psycopg2`, `python-redis`) as well as `python-wxpython` itself are in
Arch's official `extra` repo, not AUR - confirmed by looking them up before
writing the PKGBUILDs, so none of this depends on some other AUR
maintainer's package staying up to date.

## Why `-git` (VCS) packages

None of the apps have version tags yet, and this is a monorepo (one PKGBUILD
per app, all pointing at the same repo). A VCS package sidesteps both: it
clones `main` and derives `pkgver` from the commit itself
(`r<commit-count>.<short-hash>`, computed in each PKGBUILD's `pkgver()`), so
there's nothing to keep in sync manually as long as the PKGBUILD's own
metadata (deps, description, install layout) doesn't need to change. If
proper per-app release tags get introduced later, these can be converted to
regular (non-`-git`) packages sourced from a release tarball instead - but
that's a bigger change to how releases are cut, not just to packaging, so
wasn't done as part of this.

## How install works (all four PKGBUILDs follow this same shape)

Every app's top-level Python package is literally named `app` (see e.g.
`my-docker-viewer/app/frame.py`). Installing four different `app` packages
into Python's shared `site-packages` would collide, so `package()` instead
vendors each app's `main.py` + `app/` tree under its own private
`/usr/lib/<binary>/` directory, and installs a thin launcher shell script
(`aur/<binary>.sh`, one per app) at `/usr/bin/<binary>` that sets
`PYTHONPATH=/usr/lib/<binary>` before running `python3
/usr/lib/<binary>/main.py` - same effect as a venv, without needing one. Each
app's own `app/db/paths.py::project_root()` already resolves bundled
resources (the SQL migrations under `app/db/migrations/`) relative to
`main.py`'s own directory, so this layout needs no source changes - verified
by replicating `package()`'s copy step locally and importing the app from
the resulting tree.

The `.desktop`/`.sh` files live in *this* repo, under each app's `aur/`
directory - not in the AUR git repo itself (see below). `PKGBUILD`'s
`package()` step reaches them via the same clone it already made of this
repo (`git+https://github.com/cancerberoSgx/desktop-app-samples.git`), so
there is nothing extra to fetch or keep in sync.

## Testing a PKGBUILD locally before publishing

`source=` in each PKGBUILD points at this repo's public GitHub URL, so
uncommitted local changes won't be picked up by a normal `makepkg` run -
`makepkg` clones fresh from that URL into its own build dir. To test local
changes before pushing them anywhere:

1. Commit your changes locally (a push isn't required, just a commit - the
   clone below reads from `.git`, not the working tree).
2. In the app's `aur/PKGBUILD`, temporarily point `source` at your local
   clone instead of GitHub, e.g.:
   ```
   source=("$pkgname::git+file:///home/you/desktop-app-samples#branch=main")
   ```
3. From that `aur/` directory: `makepkg -si` (builds and installs in one
   step) or just `makepkg` followed by `sudo pacman -U <pkgname>-*.pkg.tar.zst`.
4. Run the installed binary (`mydockerviewer`, etc.) and confirm it starts.
5. Revert the `source=` line back to the GitHub URL, and regenerate
   `.SRCINFO` (`makepkg --printsrcinfo > .SRCINFO`) before committing -
   `.SRCINFO` must always match the current `PKGBUILD`.

`makepkg --printsrcinfo` (no build, just re-derives `.SRCINFO` from
`PKGBUILD`) is also how the checked-in `.SRCINFO` files here were generated,
and is safe to run any time without network access.

## Publishing a package to AUR (first time, per app)

1. Create an AUR account at https://aur.archlinux.org and add an SSH public
   key to it, if you haven't already.
2. `git clone ssh://aur@aur.archlinux.org/<pkgname>.git`, e.g.
   `ssh://aur@aur.archlinux.org/my-docker-viewer-git.git` - the first clone
   of a not-yet-existing package name is an empty repo; pushing to it is
   what actually creates the AUR package page.
3. Copy just that app's `PKGBUILD` and `.SRCINFO` into the cloned repo - not
   the `.desktop`/`.sh` files, which AUR never needs directly (see above).
4. `git add PKGBUILD .SRCINFO && git commit -m "Initial import" && git push`.
5. Repeat per app/pkgname.

## Updating a published package

Because these are `-git` packages, an ordinary app change (new feature, bug
fix, anything under `my-docker-viewer/app/`, etc.) needs **no action here** -
anyone who rebuilds with `yay -S my-docker-viewer-git` (or `makepkg -si`)
gets `main`'s current HEAD automatically, `pkgver()` recomputes itself. You
only need to push an update to the AUR git repo when the **PKGBUILD itself**
changes - a new/renamed dependency, a renamed binary, a changed install
path, etc. In that case: regenerate `.SRCINFO`
(`makepkg --printsrcinfo > .SRCINFO`), commit both files in the AUR repo
clone, and push.
