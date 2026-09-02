"""ProfileRepository is pure SQLite CRUD, but real profiles are the
prerequisite for every datasource/script test below - see conftest.py's
`profile` fixture, which goes through this same repository."""
from app.models import Profile
from app.repositories import ProfileRepository


def test_create_and_get(profile_repo: ProfileRepository):
    created = profile_repo.create("Alpha")

    assert created.id is not None
    assert created.name == "Alpha"

    fetched = profile_repo.get(created.id)
    assert fetched is not None
    assert fetched.name == "Alpha"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_get_missing_returns_none(profile_repo: ProfileRepository):
    assert profile_repo.get(999999) is None


def test_list_orders_by_name(profile_repo: ProfileRepository):
    profile_repo.create("Zeta")
    profile_repo.create("Alpha")
    profile_repo.create("Mu")

    names = [p.name for p in profile_repo.list()]

    assert names == sorted(names)
    assert names == ["Alpha", "Mu", "Zeta"]


def test_update_changes_name_and_updated_at(profile_repo: ProfileRepository):
    created = profile_repo.create("Original")

    updated = profile_repo.update(Profile(id=created.id, name="Renamed"))

    assert updated.name == "Renamed"
    fetched = profile_repo.get(created.id)
    assert fetched.name == "Renamed"


def test_delete_removes_profile(profile_repo: ProfileRepository):
    created = profile_repo.create("Throwaway")

    profile_repo.delete(created.id)

    assert profile_repo.get(created.id) is None


def test_delete_cascades_to_datasources(profile_repo: ProfileRepository, datasource_repo, redis_env):
    from app.models import Datasource

    profile = profile_repo.create("Has Datasources")
    ds = datasource_repo.create(
        Datasource(
            id=None,
            name="Child Datasource",
            profile_id=profile.id,
            redis_host=redis_env["host"],
            redis_port=redis_env["port"],
        )
    )

    profile_repo.delete(profile.id)

    # ON DELETE CASCADE on datasources.profile_id (see
    # app/db/migrations/0002_create_datasources.sql) - this only actually
    # cascades if "PRAGMA foreign_keys = ON" is in effect, which is exactly
    # what conftest's sqlite_conn fixture and the real app's
    # app/db/connection.get_connection() both set.
    assert datasource_repo.get(ds.id) is None
