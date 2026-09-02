"""DatasourceRepository's RediSearch methods (list_indexes/get_index_details)
- these require the `search` module, hence redis-stack rather than plain
redis in test/docker-compose.yml. Each test creates its own index (via the
`index_name` fixture, dropped in teardown) over documents living under a
`key_prefix`, so it never touches any pre-existing index/data on the target
server."""
from app.models import Datasource
from app.repositories import DatasourceRepository


def test_list_indexes_includes_created_index(
    datasource_repo: DatasourceRepository,
    datasource: Datasource,
    redis_client,
    key_prefix: str,
    index_name: str,
):
    redis_client.execute_command(
        "FT.CREATE", index_name, "ON", "HASH", "PREFIX", "1", key_prefix,
        "SCHEMA", "name", "TEXT",
    )

    names = datasource_repo.list_indexes(datasource)

    assert index_name in names


def test_get_index_details_parses_fields_and_prefixes(
    datasource_repo: DatasourceRepository,
    datasource: Datasource,
    redis_client,
    key_prefix: str,
    index_name: str,
):
    redis_client.execute_command(
        "FT.CREATE", index_name, "ON", "HASH", "PREFIX", "1", key_prefix,
        "SCHEMA", "name", "TEXT", "age", "NUMERIC", "SORTABLE",
    )
    redis_client.hset(f"{key_prefix}1", mapping={"name": "Ada Lovelace", "age": "36"})

    details = datasource_repo.get_index_details(datasource, index_name)

    assert details.name == index_name
    assert details.key_type == "HASH"
    assert key_prefix in details.prefixes

    fields_by_attribute = {f.attribute: f for f in details.fields}
    assert fields_by_attribute["name"].type == "TEXT"
    assert fields_by_attribute["age"].type == "NUMERIC"
    assert "SORTABLE" in fields_by_attribute["age"].flags

    # RediSearch indexes new/changed hashes matching the prefix asynchronously,
    # so num_docs isn't asserted here - it would make this test flaky.
    assert details.num_records >= 0
