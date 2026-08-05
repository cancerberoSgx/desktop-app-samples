import sys
from pathlib import Path

APP_DIR_NAME = ".my-docker-viewer"
DB_FILE_NAME = "my-docker-viewer.db"


def project_root() -> Path:
    """Root used to locate bundled resources (e.g. migrations).

    In a PyInstaller build, `sys._MEIPASS` points at the extracted/collected
    resource root; in a normal source checkout it's the directory containing
    the `app` package.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


def migrations_dir() -> Path:
    return project_root() / "app" / "db" / "migrations"


def app_data_dir() -> Path:
    """Per-user data directory, created on first access."""
    path = Path.home() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return app_data_dir() / DB_FILE_NAME
