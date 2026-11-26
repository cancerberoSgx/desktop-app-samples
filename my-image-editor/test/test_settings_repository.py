"""SettingsRepository is pure SQLite key/value CRUD."""
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.repositories import SettingsRepository


def test_get_missing_key_returns_none(settings_repo: SettingsRepository):
    assert settings_repo.get("does-not-exist") is None


def test_set_overwrites_existing_value(settings_repo: SettingsRepository):
    settings_repo.set("some_key", "first")
    settings_repo.set("some_key", "second")
    assert settings_repo.get("some_key") == "second"


def test_bool_and_int_helpers(settings_repo: SettingsRepository):
    assert settings_repo.get_bool("flag", default=True) is True
    settings_repo.set_bool("flag", False)
    assert settings_repo.get_bool("flag", default=True) is False

    assert settings_repo.get_int("n") is None
    settings_repo.set_int("n", 7)
    assert settings_repo.get_int("n") == 7
    settings_repo.set("n", "garbage")
    assert settings_repo.get_int("n") is None


def test_get_int_in_range(settings_repo: SettingsRepository):
    assert settings_repo.get_int_in("q", 90, 1, 100) == 90
    settings_repo.set_int("q", 70)
    assert settings_repo.get_int_in("q", 90, 1, 100) == 70
    settings_repo.set_int("q", 500)
    assert settings_repo.get_int_in("q", 90, 1, 100) == 90


def test_migrations_are_applied_once(conn):
    run_migrations(conn, migrations_dir())  # second run is a no-op
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == ["0001_create_settings"]
