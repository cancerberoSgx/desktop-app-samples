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
- **`docs/<app>/index.html`'s version label** (the four apps that have a
  homepage - my-data-viewer, my-disk-viewer, my-docker-viewer,
  my-redis-viewer) shows the current version next to the download buttons,
  e.g. `v1.0.1`, as a `<span id="version">` near the
  `downloads-note` div. The download buttons themselves keep pointing at
  the stable `<binary>-latest` release regardless - that release's own
  asset filenames are already unversioned/permanent (see above), so
  nothing about the *links* needs to track the version; the label is
  purely a human-readable "this is what you'd get" indicator, and it would
  otherwise have no way to stay accurate since the docs are static HTML
  with no build step of their own.

  That label is kept in sync by CI, not by hand: each app's
  `.github/workflows/<app>-build.yml` `release` job ends with a step that
  only runs when the job was triggered by a real `<app>-v*` tag push
  (never for a `workflow_dispatch` test run), which checks out `main`
  separately from the tag being built, `sed`-replaces the `<span
  id="version">` text with the version being released, and - if that
  changed anything - commits and pushes it straight back to `main` as
  `github-actions[bot]`. GitHub Pages here serves directly from
  `main`/`docs` (no separate Pages deploy workflow), so that push alone is
  enough to update the live site. This can't loop: the workflow only
  triggers on tag pushes, never on pushes to `main`, so its own commit-back
  never re-triggers it.

  (A client-side alternative - fetching version info from GitHub's
  Releases API in the browser instead - was considered and rejected: the
  `-latest` release's own tag/assets carry no version info by design, so
  the browser would have to fetch the whole releases list, filter for
  `<app>-v*` tags, and pick the highest semver itself, adding runtime
  complexity, an API round-trip before the label appears, and a dependency
  on GitHub's unauthenticated rate limit - for no benefit over CI just
  writing the value it already knows.)
