"""SettingsRepository is pure SQLite key/value CRUD."""
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
