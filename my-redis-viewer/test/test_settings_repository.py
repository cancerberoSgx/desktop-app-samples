"""SettingsRepository is pure SQLite key/value CRUD - no Redis involved."""
from app.repositories import SettingsRepository


def test_get_missing_key_returns_none(settings_repo: SettingsRepository):
    assert settings_repo.get("does-not-exist") is None


def test_set_then_get_roundtrips(settings_repo: SettingsRepository):
    settings_repo.set("some_key", "some_value")

    assert settings_repo.get("some_key") == "some_value"


def test_set_overwrites_existing_value(settings_repo: SettingsRepository):
    settings_repo.set("some_key", "first")
    settings_repo.set("some_key", "second")

    assert settings_repo.get("some_key") == "second"


def test_current_profile_id_roundtrip(settings_repo: SettingsRepository):
    assert settings_repo.get_current_profile_id() is None

    settings_repo.set_current_profile_id(42)
    assert settings_repo.get_current_profile_id() == 42

    settings_repo.set_current_profile_id(None)
    assert settings_repo.get_current_profile_id() is None


def test_last_datasource_id_roundtrip(settings_repo: SettingsRepository):
    settings_repo.set_last_datasource_id(7)
    assert settings_repo.get_last_datasource_id() == 7


def test_sidebar_collapsed_roundtrip(settings_repo: SettingsRepository):
    assert settings_repo.get_sidebar_collapsed() is False

    settings_repo.set_sidebar_collapsed(True)
    assert settings_repo.get_sidebar_collapsed() is True

    settings_repo.set_sidebar_collapsed(False)
    assert settings_repo.get_sidebar_collapsed() is False
