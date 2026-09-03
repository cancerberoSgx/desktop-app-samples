# AUR packaging

Each of the five wxPython apps in this repo has an `aur/` directory with a
PKGBUILD that installs it as a normal Arch Linux package - dynamically linked
against the distro's own `python`/`python-wxpython`/etc, nothing bundled.
Four of the five source straight from `main` as `-git` (VCS) packages; the
fifth, my-redis-viewer, is versioned off release tags instead - see "Why
`-git` (VCS) packages" below for the distinction. Either way this is
deliberately a different distribution path from the PyInstaller builds in
`.github/workflows/*.yml`: those target machines with no package manager
guarantee (generic Linux, Windows, macOS) and so vendor a private copy of the
interpreter and every shared library; on Arch, pacman already guarantees
those are present, so bundling them would only add size and duplicate memory
for no benefit. See each app's `CLAUDE.md`/README for how the PyInstaller
path works; this file covers the AUR path only.

| App | `aur/` PKGBUILD | AUR pkgname | Binary | Extra runtime deps |
|---|---|---|---|---|
| my-data-viewer | `my-data-viewer/aur/PKGBUILD` | `my-data-viewer-git` | `mydataviewer` | `python-duckdb`, `python-sqlalchemy`, `python-psycopg2` |
| my-disk-viewer | `my-disk-viewer/aur/PKGBUILD` | `my-disk-viewer-git` | `mydiskviewer` | - |
| my-docker-viewer | `my-docker-viewer/aur/PKGBUILD` | `my-docker-viewer-git` | `mydockerviewer` | optional `docker` (the CLI it shells out to) |
| my-documents-viewer | `my-documents-viewer/aur/PKGBUILD` | `my-documents-viewer-git` | `mydocumentsviewer` | `python-fastembed`, `python-sqlite-vec` |
| my-redis-viewer | `my-redis-viewer/aur/PKGBUILD` | `my-redis-viewer` (versioned, see below) | `myredisviewer` | `python-redis` |

The `python-duckdb`, `python-sqlalchemy`, `python-psycopg2` and `python-redis`
deps, as well as `python-wxpython` itself, are all in Arch's official
`extra` repo, not AUR - confirmed by looking them up before writing the
PKGBUILDs, so none of those depend on some other AUR maintainer's package
staying up to date. my-documents-viewer's two extra deps are the exception:
`python-fastembed` and `python-sqlite-vec` only exist as AUR packages
themselves (also confirmed by looking them up) - unavoidable since fastembed
and sqlite-vec aren't in Arch's official repos at all, but it does mean an
AUR helper (`yay`/`paru`) is needed to resolve them automatically; plain
`makepkg` would require building those two first.

## Why `-git` (VCS) packages

Four of the five apps (all but my-redis-viewer) don't have version tags yet,
and this is a monorepo (one PKGBUILD per app, all pointing at the same
repo). A VCS package sidesteps both: it clones `main` and derives `pkgver`
from the commit itself (`r<commit-count>.<short-hash>`, computed in each
PKGBUILD's `pkgver()`), so there's nothing to keep in sync manually as long
as the PKGBUILD's own metadata (deps, description, install layout) doesn't
need to change. Once an app gets proper per-app release tags, its `-git`
package can be converted to a regular (non-`-git`) one sourced from a tagged
commit instead - see the my-redis-viewer section below, which is the first
app to make that switch.

## Per-app semver + the my-redis-viewer versioned package

my-redis-viewer is versioned with tags shaped `my-redis-viewer-v<semver>`
(e.g. `my-redis-viewer-v1.0.0`) - independently of the other apps in this
monorepo, each of which will get its own tag namespace and version counter
whenever it makes the same switch. The version's source of truth is the
plain-text `my-redis-viewer/VERSION` file (currently `1.0.0`), not the tag
alone: a PyInstaller-frozen build has no `.git` to read a version from at
runtime, so anything the app itself wants to show (an About screen, say)
has to bake in a value from that file at build time instead.

Cutting a new my-redis-viewer release:

1. `scripts/bump-app-version.py my-redis-viewer 1.0.1` - updates
   `my-redis-viewer/VERSION` and the `pkgver=`/`pkgrel=` lines in
   `my-redis-viewer/aur/PKGBUILD` together (so they can't drift out of
   sync), and regenerates `.SRCINFO`. Use `--patch` instead of a version to
   just increment the current patch number (e.g. `1.0.1` -> `1.0.2`); for a
   packaging-only fix at the same upstream version (no app change), use
   `--pkgrel-only` instead - it just bumps `pkgrel` and leaves `VERSION`
   untouched. The script prints the exact `git` commands to run next; it
   doesn't commit, tag, or push anything itself. See the "Versioning"
   section in the root `README.md` for the cross-app overview.
2. `git add` the three changed files, commit, then
   `git tag my-redis-viewer-v1.0.1 && git push && git push --tags`.
3. `.github/workflows/my-redis-viewer-build.yml`'s `check-version` job fails
   the run if the pushed tag doesn't match the VERSION file, then `build`
   produces the three PyInstaller zips and `release` publishes them as a
   GitHub Release named after the tag itself (plus the usual rolling
   `myredisviewer-latest`).
4. Push the already-updated `PKGBUILD` + `.SRCINFO` to the AUR git repo (see
   "Updating a published package" below) - this step is still manual, same
   as any other AUR maintainer update.

A `workflow_dispatch` run (no tag) still works exactly as before, for ad hoc
test builds - it falls back to the old `myredisviewer-build.<run number>`
release naming instead of a semver one.

## How install works (all five PKGBUILDs follow this same shape)

Every app's top-level Python package is literally named `app` (see e.g.
`my-docker-viewer/app/frame.py`). Installing five different `app` packages
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
   `ssh://aur@aur.archlinux.org/my-docker-viewer-git.git` (or, for the
   versioned my-redis-viewer package, `.../my-redis-viewer.git` - no `-git`
   suffix, since it isn't a VCS package) - the first clone of a
   not-yet-existing package name is an empty repo; pushing to it is what
   actually creates the AUR package page.
3. Copy just that app's `PKGBUILD` and `.SRCINFO` into the cloned repo - not
   the `.desktop`/`.sh` files, which AUR never needs directly (see above).
4. `git add PKGBUILD .SRCINFO && git commit -m "Initial import" && git push`.
5. Repeat per app/pkgname.

## Updating a published package

Because the four remaining apps are `-git` packages, an ordinary app change
(new feature, bug fix, anything under `my-docker-viewer/app/`, etc.) needs
**no action here** - anyone who rebuilds with `yay -S my-docker-viewer-git`
(or `makepkg -si`) gets `main`'s current HEAD automatically, `pkgver()`
recomputes itself. You only need to push an update to the AUR git repo when
the **PKGBUILD itself** changes - a new/renamed dependency, a renamed
binary, a changed install path, etc. In that case: regenerate `.SRCINFO`
(`makepkg --printsrcinfo > .SRCINFO`), commit both files in the AUR repo
clone, and push.

my-redis-viewer, being a versioned (non-`-git`) package, is different: every
new app release needs a PKGBUILD update, since `pkgver`/the tagged `source`
ref are static rather than self-computed - see "Cutting a new my-redis-viewer
release" above.
