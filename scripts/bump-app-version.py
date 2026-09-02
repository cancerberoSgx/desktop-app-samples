#!/usr/bin/env python3
"""Bumps an app's version under the AUR "versioned package" scheme (see
AUR.md's "Per-app semver" section) - keeps <app>/VERSION, the pkgver=/
pkgrel= lines in <app>/aur/PKGBUILD, and <app>/aur/.SRCINFO all in sync
instead of hand-editing each one. Currently only my-redis-viewer uses this
scheme; run this for another app once it gets its own release tags too.

Usage:
  scripts/bump-app-version.py <app> <new-version>   # e.g. my-redis-viewer 1.0.1
  scripts/bump-app-version.py <app> --patch         # 1.0.1 -> 1.0.2, same major.minor
  scripts/bump-app-version.py <app> --pkgrel-only   # packaging-only fix, same version

Does NOT commit, tag, or push anything - prints what to run next instead,
since pushing a tag triggers a real CI build and a public GitHub Release.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def read_pkgrel(pkgbuild_text: str) -> int:
    match = re.search(r"^pkgrel=(\S+)$", pkgbuild_text, re.MULTILINE)
    if not match:
        fail("couldn't find a pkgrel= line in PKGBUILD")
    return int(match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bump an app's version (VERSION file + AUR PKGBUILD + .SRCINFO).",
    )
    parser.add_argument("app", help="app directory, e.g. my-redis-viewer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("version", nargs="?", default=None, help="new semver, e.g. 1.0.1")
    group.add_argument(
        "--patch", action="store_true",
        help="auto-increment just the patch component of the current version",
    )
    group.add_argument(
        "--pkgrel-only", action="store_true",
        help="packaging-only fix, same version - just bumps pkgrel",
    )
    args = parser.parse_args()

    app_dir = Path(args.app)
    version_file = app_dir / "VERSION"
    pkgbuild_file = app_dir / "aur" / "PKGBUILD"

    if not version_file.is_file() or not pkgbuild_file.is_file():
        fail(
            f"{args.app} isn't set up for versioned releases yet "
            f"(missing {version_file} or {pkgbuild_file})\n"
            "       see AUR.md's 'Per-app semver' section - only my-redis-viewer "
            "uses this scheme so far."
        )

    current_version = version_file.read_text().strip()
    pkgbuild_text = pkgbuild_file.read_text()
    current_pkgrel = read_pkgrel(pkgbuild_text)

    if args.pkgrel_only:
        new_version = current_version
        new_pkgrel = current_pkgrel + 1
        print(f"Packaging-only bump: {args.app} stays at {new_version}, "
              f"pkgrel {current_pkgrel} -> {new_pkgrel}")
    elif args.patch:
        if not SEMVER_RE.match(current_version):
            fail(f"current version isn't plain semver (X.Y.Z), got: {current_version}")
        major, minor, patch = current_version.split(".")
        new_version = f"{major}.{minor}.{int(patch) + 1}"
        new_pkgrel = 1
        print(f"Patch bump: {args.app} {current_version} -> {new_version} (pkgrel reset to 1)")
    else:
        new_version = args.version
        if not SEMVER_RE.match(new_version):
            fail(f"version must be plain semver (X.Y.Z), got: {new_version}")
        new_pkgrel = 1
        print(f"Version bump: {args.app} {current_version} -> {new_version} (pkgrel reset to 1)")

    version_file.write_text(f"{new_version}\n")
    pkgbuild_text = re.sub(r"^pkgver=.*$", f"pkgver={new_version}", pkgbuild_text, flags=re.MULTILINE)
    pkgbuild_text = re.sub(r"^pkgrel=.*$", f"pkgrel={new_pkgrel}", pkgbuild_text, flags=re.MULTILINE)
    pkgbuild_file.write_text(pkgbuild_text)

    srcinfo_file = app_dir / "aur" / ".SRCINFO"
    result = subprocess.run(
        ["makepkg", "--printsrcinfo"],
        cwd=app_dir / "aur",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"makepkg --printsrcinfo failed:\n{result.stderr}")
    srcinfo_file.write_text(result.stdout)

    print(f"\nUpdated: {version_file}, {pkgbuild_file}, {srcinfo_file}\n")
    print("Next steps:")
    print(f"  git add {version_file} {pkgbuild_file} {srcinfo_file}")
    if args.pkgrel_only:
        print(f'  git commit -m "{args.app}: packaging fix (pkgrel {new_pkgrel})"')
        print("  git push   # no new tag needed - same upstream version")
    else:
        print(f'  git commit -m "{args.app}: bump to {new_version}"')
        print(f"  git tag {args.app}-v{new_version}")
        print("  git push && git push --tags   # pushing the tag triggers the release build")
    print("  then update the AUR git repo clone (PKGBUILD + .SRCINFO) and push - see AUR.md")


if __name__ == "__main__":
    main()
