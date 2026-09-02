"""ScriptRepository is pure SQLite CRUD, scoped to a datasource. `datasource`
(see conftest.py) is a real row pointed at the test Redis instance, but no
script here is actually executed against Redis - that's execute_script's
job, covered in test_datasource_redis_operations.py."""
from app.models import Datasource, Script
from app.repositories import ScriptRepository


def test_create_and_get(script_repo: ScriptRepository, datasource: Datasource):
    created = script_repo.create(
        Script(id=None, name="Ping", datasource_id=datasource.id, text="PING")
    )

    assert created.id is not None

    fetched = script_repo.get(created.id)
    assert fetched.name == "Ping"
    assert fetched.text == "PING"
    assert fetched.datasource_id == datasource.id


def test_list_scoped_to_datasource_orders_by_name(
    script_repo: ScriptRepository, datasource: Datasource, profile_repo, datasource_repo, redis_env
):
    script_repo.create(Script(id=None, name="Zeta", datasource_id=datasource.id, text="PING"))
    script_repo.create(Script(id=None, name="Alpha", datasource_id=datasource.id, text="PING"))

    other_profile = profile_repo.create("Other Profile")
    other_datasource = datasource_repo.create(
        Datasource(
            id=None,
            name="Other Datasource",
            profile_id=other_profile.id,
            redis_host=redis_env["host"],
            redis_port=redis_env["port"],
        )
    )
    script_repo.create(Script(id=None, name="Should Not Appear", datasource_id=other_datasource.id, text="PING"))

    names = [s.name for s in script_repo.list(datasource.id)]

    assert names == ["Alpha", "Zeta"]


def test_update(script_repo: ScriptRepository, datasource: Datasource):
    created = script_repo.create(
        Script(id=None, name="Original", datasource_id=datasource.id, text="PING")
    )

    script_repo.update(Script(id=created.id, name="Renamed", datasource_id=datasource.id, text="GET foo"))

    fetched = script_repo.get(created.id)
    assert fetched.name == "Renamed"
    assert fetched.text == "GET foo"


def test_delete(script_repo: ScriptRepository, datasource: Datasource):
    created = script_repo.create(
        Script(id=None, name="Throwaway", datasource_id=datasource.id, text="PING")
    )

    script_repo.delete(created.id)

    assert script_repo.get(created.id) is None
