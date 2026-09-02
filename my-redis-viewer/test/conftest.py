"""Shared fixtures for the integration test suite.

These tests exercise the repository classes in app/repositories.py for real:
a real (in-memory) SQLite database with migrations applied, and a real Redis
server (see test/docker-compose.yml) - nothing here is mocked. That means:

  * A reachable Redis server is required to run most of this suite (see
    test/README.md). Tests that need it depend on the `redis_client`
    fixture, which fails fast with a clear message if PING doesn't succeed.
  * The Redis server is treated as shared, not disposable - `.env` may point
    at an instance that already has real keys/indexes on it, so nothing here
    ever runs FLUSHDB/FLUSHALL. Instead, every test that writes sample data
    gets a random key prefix (`key_prefix`) or index name (`index_name`) and
    cleans up only what it created, in a teardown that runs even if the test
    fails.
"""
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest
import redis

# Make `app` importable regardless of the directory pytest is invoked from
# (pytest's default "prepend" import mode only adds this file's own
# directory to sys.path, not the repo root, since test/ has no __init__.py).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.db.migrator import run_migrations  # noqa: E402
from app.db.paths import migrations_dir  # noqa: E402
from app.models import Datasource, Profile  # noqa: E402
from app.repositories import (  # noqa: E402
    DatasourceRepository,
    ProfileRepository,
    ScriptRepository,
    SettingsRepository,
)

import sqlite3  # noqa: E402


def _load_env_file(path: Path) -> dict:
    """Minimal `KEY=VALUE` .env parser - no python-dotenv dependency needed
    for a file this simple. Blank lines and '#' comments are skipped;
    values are not expanded/quoted."""
    values = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@pytest.fixture(scope="session")
def redis_env() -> dict:
    """Connection details for the Redis instance under test, from
    test/.env (see test/.env.example) falling back to the default local
    docker-compose instance (localhost:6379, no auth)."""
    values = _load_env_file(Path(__file__).resolve().parent / ".env")
    return {
        "host": values.get("REDIS_HOST") or "localhost",
        "port": int(values.get("REDIS_PORT") or 6379),
        "user": values.get("REDIS_USER") or None,
        "password": values.get("REDIS_PASSWORD") or None,
    }


@pytest.fixture(scope="session")
def redis_client(redis_env: dict) -> Iterator[redis.Redis]:
    """A plain redis-py client used by tests to seed/inspect sample data
    directly (independent of the repository code under test) and by the
    cleanup fixtures below. Session-scoped since it's stateless and cheap
    to share; fails the whole session up front with a clear message if the
    configured server isn't reachable, rather than failing every single
    test with a raw ConnectionError."""
    client = redis.Redis(
        host=redis_env["host"],
        port=redis_env["port"],
        username=redis_env["user"],
        password=redis_env["password"],
        decode_responses=True,
        socket_connect_timeout=5,
    )
    try:
        client.ping()
    except redis.RedisError as exc:
        pytest.fail(
            "Could not reach Redis at "
            f"{redis_env['host']}:{redis_env['port']} ({exc}). "
            "Start it with `docker compose up -d` in test/, or point "
            "test/.env at a running instance - see test/README.md.",
            pytrace=False,
        )
    yield client
    client.close()


@pytest.fixture()
def key_prefix(redis_client: redis.Redis) -> Iterator[str]:
    """A unique key prefix for one test to write sample data under, e.g.
    key_prefix + "user:1". Deletes every key under that prefix afterwards
    via SCAN (never KEYS), regardless of whether the test passed - so
    tests never leak keys into, or wipe unrelated data on, a shared Redis
    instance."""
    prefix = f"myredisviewer:test:{uuid.uuid4().hex}:"
    yield prefix
    for key in redis_client.scan_iter(match=f"{prefix}*", count=1000):
        redis_client.delete(key)


@pytest.fixture()
def index_name(redis_client: redis.Redis) -> Iterator[str]:
    """A unique RediSearch index name for one test to FT.CREATE, dropped
    (index only, not its documents - those live under a key_prefix and
    clean up on their own) afterwards regardless of test outcome."""
    name = f"myredisviewer-test-{uuid.uuid4().hex}"
    yield name
    try:
        redis_client.execute_command("FT.DROPINDEX", name)
    except redis.ResponseError:
        pass  # test never created it, or already dropped it itself


@pytest.fixture()
def sqlite_conn() -> Iterator[sqlite3.Connection]:
    """A fresh, fully-migrated SQLite database per test - in-memory, so
    every test starts from an empty schema with no cross-test state, the
    same way `app/db/connection.py` + `run_migrations()` set up the real
    on-disk database at startup."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn, migrations_dir())
    yield conn
    conn.close()


@pytest.fixture()
def profile_repo(sqlite_conn: sqlite3.Connection) -> ProfileRepository:
    return ProfileRepository(sqlite_conn)


@pytest.fixture()
def settings_repo(sqlite_conn: sqlite3.Connection) -> SettingsRepository:
    return SettingsRepository(sqlite_conn)


@pytest.fixture()
def script_repo(sqlite_conn: sqlite3.Connection) -> ScriptRepository:
    return ScriptRepository(sqlite_conn)


@pytest.fixture()
def datasource_repo(sqlite_conn: sqlite3.Connection) -> Iterator[DatasourceRepository]:
    repo = DatasourceRepository(sqlite_conn)
    yield repo
    repo.close_all_pools()  # mirrors MainFrame's shutdown path - no leaked sockets


@pytest.fixture()
def profile(profile_repo: ProfileRepository) -> Profile:
    """A real profile row, created through ProfileRepository like the app
    would (Profiles screen -> "New Profile")."""
    return profile_repo.create("Test Profile")


@pytest.fixture()
def datasource(datasource_repo: DatasourceRepository, profile: Profile, redis_env: dict) -> Datasource:
    """A real datasource row, scoped to `profile` and pointed at the Redis
    instance under test - created through DatasourceRepository like the
    Data Sources screen's "New Datasource" dialog would."""
    return datasource_repo.create(
        Datasource(
            id=None,
            name="Test Datasource",
            profile_id=profile.id,
            redis_host=redis_env["host"],
            redis_port=redis_env["port"],
            redis_user=redis_env["user"],
            redis_password=redis_env["password"],
        )
    )
