[Home page](https://cancerberosgx.github.io/desktop-app-samples/)

## Versioning

Each app in this monorepo is versioned independently, with its own semver
counter and its own git tag namespace: `<app>-v<major>.<minor>.<patch>`
(e.g. `my-redis-viewer-v1.0.1`). So far only `my-redis-viewer` uses this
scheme - the other apps still build off `main`'s current commit as `-git`
packages (see `AUR.md`'s "Why `-git` (VCS) packages" for why, and use
`my-redis-viewer` as the template for converting another app the same way).

For an app on this scheme:

- **`<app>/VERSION`** is the source of truth - a plain `X.Y.Z` string, not
  derived from git at build time. This matters because the PyInstaller
  builds in `.github/workflows/*.yml` produce a frozen executable with no
  `.git` directory, so anything the app itself needs to show (an About
  screen, say) has to read a baked-in value rather than something like
  `git describe`. In my-redis-viewer, `app/version.py::get_version()` reads
  it and both the Help > About menu item and the sidebar's About page
  display it - `VERSION` is bundled alongside `main.py`/`app/` in both the
  PyInstaller spec's `datas` and the AUR `PKGBUILD`'s `package()` so it's
  there to read either way.
- **`scripts/bump-app-version.py <app> ...`** bumps `VERSION` and the
  matching `pkgver=`/`pkgrel=` lines in `<app>/aur/PKGBUILD` together (and
  regenerates `.SRCINFO`), so the three files can't drift out of sync:
  - `scripts/bump-app-version.py my-redis-viewer 1.1.0` - set an explicit version.
  - `scripts/bump-app-version.py my-redis-viewer --patch` - bump just the patch number (e.g. `1.0.1` -> `1.0.2`).
  - `scripts/bump-app-version.py my-redis-viewer --pkgrel-only` - packaging-only fix, same version, just bumps `pkgrel`.

  It never commits, tags, or pushes anything itself - it prints the exact
  `git` commands to run next, since pushing a tag triggers a real CI build
  and a public GitHub Release.
- **Cutting a release:** run the script, review and commit the changed
  files, `git tag <app>-v<version>`, then `git push && git push --tags`.
  Pushing the tag triggers that app's GitHub Actions workflow, which
  verifies the tag matches `VERSION`, builds Linux/Windows/macOS zips named
  with the version (e.g. `myredisviewer-linux-1.0.1.zip`) - both as the
  workflow run's own artifacts and as the versioned GitHub Release's assets
  - and publishes them as a release named after the tag. The rolling
  `*-latest` release that download-button links point at is republished
  from the same zips with the version stripped back off each filename, so
  those links never need to change.
- The AUR side of this - converting a `-git` PKGBUILD to a versioned one,
  publishing it, and updating an already-published package - is documented
  in full in `AUR.md`.
