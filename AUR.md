# AUR packaging

Each of the five wxPython apps in this repo has an `aur/` directory with a
PKGBUILD that installs it as a normal Arch Linux package - dynamically linked
against the distro's own `python`/`python-wxpython`/etc, nothing bundled.
All five are now **versioned packages**, sourced from a per-app release tag
rather than the rolling `main` branch (see "Per-app semver + versioned
packages" below) - earlier revisions of these PKGBUILDs were `-git` (VCS)
packages instead, back before any app had release tags; my-redis-viewer was
the first to convert, and my-data-viewer/my-disk-viewer/my-docker-viewer/
my-documents-viewer followed. Either way this is deliberately a different
distribution path from the PyInstaller builds in `.github/workflows/*.yml`:
those target machines with no package manager guarantee (generic Linux,
Windows, macOS) and so vendor a private copy of the interpreter and every
shared library; on Arch, pacman already guarantees those are present, so
bundling them would only add size and duplicate memory for no benefit. See
each app's `CLAUDE.md`/README for how the PyInstaller path works; this file
covers the AUR path only.

`my-file-viewer` has no AUR package at all (not listed below) - it's
distributed via the PyInstaller builds only.

| App | `aur/` PKGBUILD | AUR pkgname | Binary | Extra runtime deps |
|---|---|---|---|---|
| my-data-viewer | `my-data-viewer/aur/PKGBUILD` | `my-data-viewer` | `mydataviewer` | `python-duckdb`, `python-sqlalchemy`, `python-psycopg2` |
| my-disk-viewer | `my-disk-viewer/aur/PKGBUILD` | `my-disk-viewer` | `mydiskviewer` | - |
| my-docker-viewer | `my-docker-viewer/aur/PKGBUILD` | `my-docker-viewer` | `mydockerviewer` | optional `docker` (the CLI it shells out to) |
| my-documents-viewer | `my-documents-viewer/aur/PKGBUILD` | `my-documents-viewer` | `mydocumentsviewer` | `python-fastembed`, `python-sqlite-vec` |
| my-redis-viewer | `my-redis-viewer/aur/PKGBUILD` | `my-redis-viewer` | `myredisviewer` | `python-redis` |

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

## Per-app semver + versioned packages

Every app in the table above is versioned independently, with tags shaped
`<app>-v<semver>` (e.g. `my-redis-viewer-v1.0.0`) - each app has its own tag
namespace and its own version counter, unrelated to the others. The
version's source of truth is the plain-text `<app>/VERSION` file, not the
tag alone: a PyInstaller-frozen build has no `.git` to read a version from
at runtime, so anything the app itself wants to show (the Help > About
menu, which all of them now do) has to bake in a value from that file at
build time instead.

Cutting a new release for `<app>`:

1. `scripts/bump-app-version.py <app> 1.0.1` - updates `<app>/VERSION` and
   the `pkgver=`/`pkgrel=` lines in `<app>/aur/PKGBUILD` together (so they
   can't drift out of sync), and regenerates `.SRCINFO`. Use `--patch`
   instead of a version to just increment the current patch number (e.g.
   `1.0.1` -> `1.0.2`); for a packaging-only fix at the same upstream
   version (no app change), use `--pkgrel-only` instead - it just bumps
   `pkgrel` and leaves `VERSION` untouched. The script prints the exact
   `git` commands to run next; it doesn't commit, tag, or push anything
   itself. See the "Versioning" section in the root `README.md` for the
   cross-app overview.
2. `git add` the changed files, commit, then
   `git tag <app>-v1.0.1 && git push && git push --tags`.
3. `.github/workflows/<app>-build.yml`'s `check-version` job fails the run
   if the pushed tag doesn't match `VERSION`, then `build` produces the
   three PyInstaller zips (named with the version, e.g.
   `mydiskviewer-linux-1.0.1.zip`) and `release` publishes them as a GitHub
   Release named after the tag itself (plus the usual rolling
   `<binary>-latest` release, whose asset filenames have the version
   stripped back off so its download links never need to change).
4. Push the already-updated `PKGBUILD` + `.SRCINFO` to the AUR git repo (see
   "Updating a published package" below) - this step is still manual, same
   as any other AUR maintainer update.

A `workflow_dispatch` run (no tag) still works for ad hoc test builds - it
falls back to the old `<binary>-build.<run number>` release naming instead
of a semver one.

## How install works (all five PKGBUILDs follow this same shape)

Every app's top-level Python package is literally named `app` (see e.g.
`my-docker-viewer/app/frame.py`). Installing five different `app` packages
into Python's shared `site-packages` would collide, so `package()` instead
vendors each app's `main.py` + `VERSION` + `app/` tree under its own private
`/usr/lib/<binary>/` directory, and installs a thin launcher shell script
(`aur/<binary>.sh`, one per app) at `/usr/bin/<binary>` that sets
`PYTHONPATH=/usr/lib/<binary>` before running `python3
/usr/lib/<binary>/main.py` - same effect as a venv, without needing one. Each
app's own `app/db/paths.py::project_root()` already resolves bundled
resources (the SQL migrations under `app/db/migrations/`, and `VERSION` via
`app/version.py`) relative to `main.py`'s own directory, so this layout
needs no source changes - verified by replicating `package()`'s copy step
locally and importing the app from the resulting tree.

The `.desktop`/`.sh` files live in *this* repo, under each app's `aur/`
directory - not in the AUR git repo itself (see below). `PKGBUILD`'s
`package()` step reaches them via the same clone it already made of this
repo (`git+https://github.com/cancerberoSgx/desktop-app-samples.git`), so
there is nothing extra to fetch or keep in sync.

## Testing a PKGBUILD locally before publishing

`source=` in each PKGBUILD points at a specific tag in this repo's public
GitHub URL, so uncommitted local changes (and any commit not yet tagged)
won't be picked up by a normal `makepkg` run - `makepkg` clones fresh from
that URL/tag into its own build dir. To test local changes before pushing
them anywhere:

1. Commit your changes locally and tag them (`git tag <app>-v0.0.0-test`,
   say - a push isn't required, just a local tag - the clone below reads
   from `.git`, not the working tree).
2. In the app's `aur/PKGBUILD`, temporarily point `source` at your local
   clone instead of GitHub, keeping the `#tag=` fragment, e.g.:
   ```
   source=("$pkgname::git+file:///home/you/desktop-app-samples#tag=<app>-v0.0.0-test")
   ```
3. From that `aur/` directory: `makepkg -si` (builds and installs in one
   step) or just `makepkg` followed by `sudo pacman -U <pkgname>-*.pkg.tar.zst`.
4. Run the installed binary (`mydockerviewer`, etc.) and confirm it starts.
5. Revert the `source=` line back to the GitHub URL/real tag, and
   regenerate `.SRCINFO` (`makepkg --printsrcinfo > .SRCINFO`) before
   committing - `.SRCINFO` must always match the current `PKGBUILD`.

`makepkg --printsrcinfo` (no build, just re-derives `.SRCINFO` from
`PKGBUILD`) is also how the checked-in `.SRCINFO` files here were generated,
and is safe to run any time without network access.

## Publishing a package to AUR (first time, per app)

1. Create an AUR account at https://aur.archlinux.org and add an SSH public
   key to it, if you haven't already.
2. `git clone ssh://aur@aur.archlinux.org/<pkgname>.git`, e.g.
   `ssh://aur@aur.archlinux.org/my-docker-viewer.git` - the first clone of
   a not-yet-existing package name is an empty repo; pushing to it is what
   actually creates the AUR package page.
3. Copy just that app's `PKGBUILD` and `.SRCINFO` into the cloned repo - not
   the `.desktop`/`.sh` files, which AUR never needs directly (see above).
4. `git add PKGBUILD .SRCINFO && git commit -m "Initial import" && git push`.
5. Repeat per app/pkgname.

## Updating a published package

Unlike an old-style `-git` VCS package (where an ordinary app change needs
no PKGBUILD update at all, since `pkgver()` self-computes from whatever
commit `main` is on), every one of these versioned packages needs a fresh
PKGBUILD push to AUR for **every** app release, since `pkgver` and the
tagged `source` ref are static rather than self-computed - see "Cutting a
new release" above for the full flow. A PKGBUILD-only change (a
new/renamed dependency, a renamed binary, a changed install path, etc.
with no version bump) still just needs `scripts/bump-app-version.py <app>
--pkgrel-only`, then push the regenerated `PKGBUILD` + `.SRCINFO` to the
AUR repo clone.
