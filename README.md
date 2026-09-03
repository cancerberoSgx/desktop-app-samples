[Home page](https://cancerberosgx.github.io/desktop-app-samples/)

## Versioning

Each app in this monorepo is versioned independently, with its own semver
counter and its own git tag namespace: `<app>-v<major>.<minor>.<patch>`
(e.g. `my-redis-viewer-v1.0.1`). All six apps use this scheme; five of them
(everything but my-file-viewer) also have an AUR package that's versioned
the same way - see `AUR.md` for the AUR-specific side of it.

For every app:

- **`<app>/VERSION`** is the source of truth - a plain `X.Y.Z` string, not
  derived from git at build time. This matters because the PyInstaller
  builds in `.github/workflows/*.yml` produce a frozen executable with no
  `.git` directory, so anything the app itself needs to show (an About
  screen, say) has to read a baked-in value rather than something like
  `git describe`. Each app's `app/version.py::get_version()` reads it, and
  the Help > About menu item (plus a sidebar About page, for the apps that
  have one) displays it - `VERSION` is bundled alongside `main.py`/`app/`
  in the PyInstaller spec's `datas`, and (for the five AUR-packaged apps)
  in the PKGBUILD's `package()` too, so it's there to read either way.
- **`scripts/bump-app-version.py <app> ...`** bumps `VERSION` and, for an
  app that has an AUR package, the matching `pkgver=`/`pkgrel=` lines in
  `<app>/aur/PKGBUILD` together (and regenerates `.SRCINFO`), so the files
  can't drift out of sync - for my-file-viewer (no AUR package) it only
  touches `VERSION`:
  - `scripts/bump-app-version.py my-redis-viewer 1.1.0` - set an explicit version.
  - `scripts/bump-app-version.py my-redis-viewer --patch` - bump just the patch number (e.g. `1.0.1` -> `1.0.2`).
  - `scripts/bump-app-version.py my-redis-viewer --pkgrel-only` - packaging-only fix, same version, just bumps `pkgrel` (only valid for an app with a PKGBUILD).

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
- The AUR side of this - the versioned-PKGBUILD scheme, publishing a
  package, and updating an already-published one - is documented in full
  in `AUR.md`.
