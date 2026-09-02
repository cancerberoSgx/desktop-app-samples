"""DatasourceRepository CRUD (pure SQLite, scoped to a profile) plus
test_connection, which is the one CRUD-adjacent method that actually talks
to Redis (a real PING)."""
import pytest
import redis

from app.models import Datasource, Profile
from app.repositories import DatasourceRepository


def test_create_and_get(datasource_repo: DatasourceRepository, profile: Profile, redis_env: dict):
    created = datasource_repo.create(
        Datasource(
            id=None,
            name="My Redis",
            profile_id=profile.id,
            redis_host=redis_env["host"],
            redis_port=redis_env["port"],
            redis_user=redis_env["user"],
            redis_password=redis_env["password"],
        )
    )

    assert created.id is not None

    fetched = datasource_repo.get(created.id)
    assert fetched.name == "My Redis"
    assert fetched.profile_id == profile.id
    assert fetched.redis_host == redis_env["host"]
    assert fetched.redis_port == redis_env["port"]


def test_get_missing_returns_none(datasource_repo: DatasourceRepository):
    assert datasource_repo.get(999999) is None


def test_list_scoped_to_profile(
    datasource_repo: DatasourceRepository, profile_repo, profile: Profile, redis_env: dict
):
    datasource_repo.create(Datasource(id=None, name="A", profile_id=profile.id, **_conn(redis_env)))
    datasource_repo.create(Datasource(id=None, name="B", profile_id=profile.id, **_conn(redis_env)))

    other_profile = profile_repo.create("Other")
    datasource_repo.create(Datasource(id=None, name="C", profile_id=other_profile.id, **_conn(redis_env)))

    names = [d.name for d in datasource_repo.list(profile.id)]

    assert names == ["A", "B"]


def test_list_name_contains_filters(datasource_repo: DatasourceRepository, profile: Profile, redis_env: dict):
    datasource_repo.create(Datasource(id=None, name="Production Cache", profile_id=profile.id, **_conn(redis_env)))
    datasource_repo.create(Datasource(id=None, name="Staging Cache", profile_id=profile.id, **_conn(redis_env)))
    datasource_repo.create(Datasource(id=None, name="Other", profile_id=profile.id, **_conn(redis_env)))

    names = [d.name for d in datasource_repo.list(profile.id, name_contains="Cache")]

    assert sorted(names) == ["Production Cache", "Staging Cache"]


def test_update_changes_fields(datasource_repo: DatasourceRepository, datasource: Datasource):
    datasource.name = "Renamed"

    updated = datasource_repo.update(datasource)

    assert updated.name == "Renamed"
    fetched = datasource_repo.get(datasource.id)
    assert fetched.name == "Renamed"


def test_delete(datasource_repo: DatasourceRepository, datasource: Datasource):
    datasource_repo.delete(datasource.id)

    assert datasource_repo.get(datasource.id) is None


def test_test_connection_succeeds_against_real_redis(
    datasource_repo: DatasourceRepository, datasource: Datasource
):
    # Raises on failure - not raising is the pass condition.
    datasource_repo.test_connection(datasource)


def test_test_connection_raises_on_unreachable_host(
    datasource_repo: DatasourceRepository, profile: Profile, redis_env: dict
):
    unreachable = Datasource(
        id=None,
        name="Unreachable",
        profile_id=profile.id,
        redis_host=redis_env["host"],
        redis_port=1,  # nothing listens here
    )

    with pytest.raises(redis.RedisError):
        datasource_repo.test_connection(unreachable)


def _conn(redis_env: dict) -> dict:
    return {
        "redis_host": redis_env["host"],
        "redis_port": redis_env["port"],
        "redis_user": redis_env["user"],
        "redis_password": redis_env["password"],
    }
