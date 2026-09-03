from app.db.paths import project_root

_UNKNOWN_VERSION = "unknown"


def get_version() -> str:
    """The app's version, read from the VERSION file bundled next to main.py.

    Not derived from git: a PyInstaller build has no .git directory, so
    VERSION (kept in sync by scripts/bump-app-version.py) is the only thing
    both a source checkout and a frozen build can read this from.
    """
    try:
        return (project_root() / "VERSION").read_text().strip()
    except OSError:
        return _UNKNOWN_VERSION
