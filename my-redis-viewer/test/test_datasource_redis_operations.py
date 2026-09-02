"""DatasourceRepository methods that talk to Redis for real: scan_keys,
search_keys, get_key_details, execute_script, get_server_info.

Every test seeds its own sample data under a random `key_prefix` (see
conftest.py) via a plain redis-py client, independent of the repository
code under test, and only ever asserts on the subset of results that
starts with that prefix - the target Redis instance may already have
other, unrelated keys on it."""
from app.models import Datasource
from app.repositories import DatasourceRepository


def test_scan_keys_finds_seeded_keys(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    expected = {f"{key_prefix}a", f"{key_prefix}b", f"{key_prefix}c"}
    for key in expected:
        redis_client.set(key, "value")

    result = datasource_repo.scan_keys(datasource)

    found = {k for k in result.keys if k.startswith(key_prefix)}
    assert found == expected
    assert result.truncated is False


def test_search_keys_matches_pattern(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    redis_client.set(f"{key_prefix}user:1", "value")
    redis_client.set(f"{key_prefix}user:2", "value")
    redis_client.set(f"{key_prefix}order:1", "value")

    result = datasource_repo.search_keys(datasource, pattern=f"{key_prefix}user:*")

    assert set(result.keys) == {f"{key_prefix}user:1", f"{key_prefix}user:2"}


def test_search_keys_filters_by_type(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    redis_client.set(f"{key_prefix}a-string", "value")
    redis_client.hset(f"{key_prefix}a-hash", mapping={"field": "value"})

    result = datasource_repo.search_keys(datasource, pattern=f"{key_prefix}*", redis_type="hash")

    assert result.keys == [f"{key_prefix}a-hash"]


def test_get_key_details_string_with_ttl(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    key = f"{key_prefix}greeting"
    redis_client.set(key, "hello world", ex=120)

    details = datasource_repo.get_key_details(datasource, key)

    assert details.exists is True
    assert details.type == "string"
    assert details.value_text == "hello world"
    assert details.ttl_seconds is not None and 0 < details.ttl_seconds <= 120


def test_get_key_details_missing_key(
    datasource_repo: DatasourceRepository, datasource: Datasource, key_prefix: str
):
    details = datasource_repo.get_key_details(datasource, f"{key_prefix}does-not-exist")

    assert details.exists is False
    assert details.type == "none"


def test_get_key_details_hash(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    key = f"{key_prefix}profile"
    redis_client.hset(key, mapping={"name": "Ada", "role": "engineer"})

    details = datasource_repo.get_key_details(datasource, key)

    assert details.type == "hash"
    assert set(details.hash_fields) == {("name", "Ada"), ("role", "engineer")}


def test_get_key_details_json(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    key = f"{key_prefix}doc"
    redis_client.json().set(key, "$", {"name": "Ada", "tags": ["a", "b"]})

    details = datasource_repo.get_key_details(datasource, key)

    assert details.type == "ReJSON-RL"
    assert details.json_value == {"name": "Ada", "tags": ["a", "b"]}


def test_get_key_details_list(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    key = f"{key_prefix}queue"
    redis_client.rpush(key, "first", "second", "third")

    details = datasource_repo.get_key_details(datasource, key)

    assert details.type == "list"
    assert details.value_text == "[0] first\n[1] second\n[2] third"


def test_execute_script_runs_commands_in_order_and_reports_errors(
    datasource_repo: DatasourceRepository, datasource: Datasource, key_prefix: str
):
    key = f"{key_prefix}counter"
    script = f"SET {key} 1\nINCR {key}\nNOTACOMMAND {key}\nGET {key}"

    results = datasource_repo.execute_script(datasource, script)

    assert [r.is_error for r in results] == [False, False, True, False]
    assert results[1].output_text == "2"
    assert results[3].output_text == "2"


def test_execute_script_keys_command_returns_key_list(
    datasource_repo: DatasourceRepository, datasource: Datasource, redis_client, key_prefix: str
):
    redis_client.set(f"{key_prefix}a", "1")
    redis_client.set(f"{key_prefix}b", "1")

    results = datasource_repo.execute_script(datasource, f"KEYS {key_prefix}*")

    assert results[0].keys == [f"{key_prefix}a", f"{key_prefix}b"]


def test_get_server_info_has_expected_sections(
    datasource_repo: DatasourceRepository, datasource: Datasource
):
    info = datasource_repo.get_server_info(datasource)

    assert "redis_version" in info
    assert "used_memory" in info
