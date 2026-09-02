# Integration tests

Pytest suite that exercises the repository classes in `app/repositories.py`
for real: a real (in-memory, freshly migrated) SQLite database, and a real
Redis server - no mocking. Every test that needs Redis creates its own
profile/datasource rows through `ProfileRepository`/`DatasourceRepository`
and its own sample keys/indexes under a randomly generated prefix, and
cleans that data up afterwards - it never touches or wipes anything else
already on the target server.

## 1. Start a Redis instance

The suite needs a real, reachable Redis server with the RedisJSON and
RediSearch modules loaded (some tests exercise `get_key_details` on a
RedisJSON key, and `list_indexes`/`get_index_details`, which are
RediSearch-only commands) - that's why this folder's `docker-compose.yml`
uses `redis/redis-stack`, not plain `redis`.

```bash
cd test
docker compose up -d
```

This is equivalent to:

```bash
docker run -d --name redis-stack -p 6379:6379 -p 8001:8001 redis/redis-stack:latest
```

Already have a redis-stack instance running some other way (including one
you started manually, matching the `docker run` above)? Skip this step -
the tests just need something reachable at the host/port from step 2, and
never assume they're the only thing using it.

Tear it down when you're done:

```bash
docker compose down
```

## 2. Point the tests at it (optional)

By default the tests connect to `localhost:6379` with no username/password,
which matches both the docker-compose service above and a bare
`redis/redis-stack` container. If you need something else (a different
host/port, or a server with auth configured):

```bash
cp .env.example .env
# edit .env
```

`.env` is git-ignored and read by `test/conftest.py` only - it has no
effect on the app itself.

## 3. Install dependencies

From the project root, with the project's venv active (see the main
[README](../README.md)):

```bash
pip install -r requirements.txt -r test/requirements.txt
```

## 4. Run the tests

From the project root:

```bash
pytest test/
```

or from this folder:

```bash
cd test
pytest
```

A `redis_client` fixture pings the configured server once, up front, and
fails the whole session with a clear message (rather than every individual
test with a raw connection error) if it can't be reached.

## Notes

- Every SQLite-backed test gets its own in-memory database with migrations
  applied fresh (see `app/db/migrator.py`) - no state leaks between tests
  and nothing touches your real `~/.my-redis-viewer/my-redis-viewer.db`.
- Every Redis-backed test that writes sample keys does so under a random
  `myredisviewer:test:<uuid>:` prefix (the `key_prefix` fixture) and deletes
  only those keys afterwards; RediSearch index tests do the same for
  indexes (the `index_name` fixture, dropped via `FT.DROPINDEX`). Neither
  fixture ever runs `FLUSHDB`/`FLUSHALL`.
- `test_datasource_repository.py::test_test_connection_raises_on_unreachable_host`
  deliberately points at a closed port and expects a connection failure -
  it doesn't depend on the Redis instance from step 1.
