"""Shared fixtures. Everything is real (in-memory SQLite, Pillow images,
files written to tmp_path) - nothing is mocked, nothing needs a display."""
import sqlite3
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.db.migrator import run_migrations  # noqa: E402
from app.db.paths import migrations_dir  # noqa: E402
from app.editor.document import Document  # noqa: E402
from app.editor.editor import Editor  # noqa: E402
from app.repositories import SettingsRepository  # noqa: E402

WHITE = (255, 255, 255, 255)
RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    run_migrations(connection, migrations_dir())
    yield connection
    connection.close()


@pytest.fixture
def settings_repo(conn) -> SettingsRepository:
    return SettingsRepository(conn)


def blank(width: int = 200, height: int = 100, color=WHITE) -> Image.Image:
    return Image.new("RGBA", (width, height), color)


@pytest.fixture
def doc() -> Document:
    return Document(blank())


@pytest.fixture
def editor(doc) -> Editor:
    ed = Editor()
    ed.set_document(doc)
    return ed
