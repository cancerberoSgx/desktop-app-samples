import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import redis

from .models import Datasource, Profile, Script
from .redis_command_parser import parse_commands
from .redis_value_format import (
    build_value_text,
    fetch_hash_fields,
    fetch_json_value,
    format_command_result,
)

CURRENT_PROFILE_SETTING_KEY = "current_profile_id"
LAST_DATASOURCE_SETTING_KEY = "last_datasource_id"
SIDEBAR_COLLAPSED_SETTING_KEY = "sidebar_collapsed"
CONNECTION_TIMEOUT_SECONDS = 5
# SCAN's COUNT hint - how many keys each round trip asks the server to walk.
# The server-side cost of one SCAN call is ~proportional to this regardless
# of total keyspace size, so raising it is close to free on the server; what
# it buys the client is fewer round trips (wall clock for a full scan is
# dominated by round-trip latency, not server work - see scan_keys below).
KEY_SCAN_BATCH_SIZE = 5000
KEY_SCAN_LIMIT = 200_000


@dataclass
class KeyScanResult:
    keys: List[str]
    truncated: bool


@dataclass
class KeyDetails:
    key: str
    exists: bool
    type: str
    ttl_seconds: Optional[int]
    encoding: Optional[str]
    memory_bytes: Optional[int]
    idle_seconds: Optional[int]
    value_text: str
    value_truncated: bool
    # Populated only for their respective types - see get_key_details.
    # KeyDetailsDialog uses these for the Table/Json tabs; value_text
    # above still carries the flattened/pretty-printed fallback text.
    hash_fields: Optional[List[Tuple[str, str]]] = None
    json_value: Optional[Any] = None


@dataclass
class CommandExecutionResult:
    line_number: int
    command_text: str
    output_text: str
    is_error: bool
    keys: Optional[List[str]] = None


@dataclass
class IndexField:
    """One entry from FT.INFO's `attributes` list - a field the index was
    created with."""

    identifier: str
    attribute: str
    type: str
    flags: List[str]
    extra: dict


@dataclass
class IndexDetails:
    """Parsed FT.INFO output for a single RediSearch index."""

    name: str
    key_type: Optional[str]
    prefixes: List[str]
    fields: List[IndexField]
    num_docs: int
    num_records: int
    indexing: bool
    percent_indexed: Optional[float]
    hash_indexing_failures: int


class DatasourceRepository:
    """CRUD for `datasources` (pure SQL against SQLite), scoped to a profile,
    plus `test_connection`, which opens a real connection to the Redis server
    described by the record and issues a PING.

    Every live Redis operation below goes through `_make_client`, which
    hands out a `redis.Redis` backed by a per-datasource `redis.ConnectionPool`
    cached in `self._pools` (keyed by datasource id + decode_responses, since
    that flag is baked into a pool's connection kwargs) instead of opening a
    fresh TCP connection (and, for authenticated servers, re-running AUTH)
    on every single call. `client.close()` at each call site still does the
    right thing here - passing `connection_pool=` explicitly makes redis-py
    treat the pool as caller-owned, so `close()` just returns the connection
    to the pool instead of tearing it down (see redis.Redis.close). Pools are
    invalidated on update()/delete() (host/port/credentials may have changed,
    or the datasource is gone) and all of them are dropped via
    close_all_pools() on app shutdown."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._pools: Dict[Tuple[int, bool], redis.ConnectionPool] = {}
        self._pools_lock = threading.Lock()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(self, datasource: Datasource) -> Datasource:
        cursor = self._conn.execute(
            """
            INSERT INTO datasources
                (name, profile_id, redis_host, redis_port, redis_user, redis_password)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datasource.name,
                datasource.profile_id,
                datasource.redis_host,
                datasource.redis_port,
                datasource.redis_user,
                datasource.redis_password,
            ),
        )
        self._conn.commit()
        datasource.id = cursor.lastrowid
        return datasource

    def list(self, profile_id: int, name_contains: Optional[str] = None) -> List[Datasource]:
        query = "SELECT * FROM datasources WHERE profile_id = ?"
        params: list = [profile_id]
        if name_contains:
            query += " AND name LIKE ?"
            params.append(f"%{name_contains}%")
        query += " ORDER BY name"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_datasource(row) for row in rows]

    def get(self, datasource_id: int) -> Optional[Datasource]:
        row = self._conn.execute(
            "SELECT * FROM datasources WHERE id = ?", (datasource_id,)
        ).fetchone()
        return self._row_to_datasource(row) if row else None

    def update(self, datasource: Datasource) -> Datasource:
        self._conn.execute(
            """
            UPDATE datasources
            SET name = ?, redis_host = ?, redis_port = ?, redis_user = ?, redis_password = ?
            WHERE id = ?
            """,
            (
                datasource.name,
                datasource.redis_host,
                datasource.redis_port,
                datasource.redis_user,
                datasource.redis_password,
                datasource.id,
            ),
        )
        self._conn.commit()
        self._invalidate_pool(datasource.id)
        return datasource

    def delete(self, datasource_id: int) -> None:
        self._conn.execute("DELETE FROM datasources WHERE id = ?", (datasource_id,))
        self._conn.commit()
        self._invalidate_pool(datasource_id)

    @staticmethod
    def _row_to_datasource(row: sqlite3.Row) -> Datasource:
        return Datasource(
            id=row["id"],
            name=row["name"],
            profile_id=row["profile_id"],
            redis_host=row["redis_host"],
            redis_port=row["redis_port"],
            redis_user=row["redis_user"],
            redis_password=row["redis_password"],
        )

    # ------------------------------------------------------------------
    # Live connection check - no data exploration, just PING.
    # ------------------------------------------------------------------
    def test_connection(self, datasource: Datasource) -> None:
        """Open a connection to `datasource`'s Redis server and PING it.
        Raises on any failure (connection refused, auth error, timeout...);
        the caller is expected to catch and display the exception."""
        client = self._make_client(datasource)
        try:
            if not client.ping():
                raise ConnectionError("PING did not return a successful response.")
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Key discovery for the Data Explorer tree view.
    # ------------------------------------------------------------------
    def scan_keys(
        self,
        datasource: Datasource,
        limit: int = KEY_SCAN_LIMIT,
        batch_size: int = KEY_SCAN_BATCH_SIZE,
        on_progress: Optional[Callable[[List[str]], None]] = None,
    ) -> KeyScanResult:
        """Walk the whole keyspace with SCAN (never KEYS, which blocks the
        server) and return every key name, capped at `limit` so a
        pathologically large keyspace can't hang the scan indefinitely.

        `on_progress(new_keys)` is invoked once per completed batch (plus
        once more for a trailing partial batch, if any, right before this
        method returns) with just the keys collected since the previous
        call - so every scanned key is delivered to the caller exactly
        once, in order, as it's found rather than only once the entire
        (possibly slow, on a high-latency connection) scan has finished.
        Invoked from this method's caller's thread - the caller is
        responsible for hopping back to the UI thread, e.g. via
        wx.CallAfter, and can use this to render results (a running count,
        a partially-filled tree/list) progressively instead of blocking
        the UI on the whole scan. Note this only spreads out *when* the
        client sees results - it doesn't change how much work SCAN does on
        the server, which is already cheap and bounded per call regardless
        (see the KEY_SCAN_BATCH_SIZE comment above)."""
        client = self._make_client(datasource, decode_responses=True)
        keys: List[str] = []
        pending: List[str] = []
        truncated = False
        try:
            for key in client.scan_iter(count=batch_size):
                keys.append(key)
                pending.append(key)
                if on_progress and len(pending) >= batch_size:
                    on_progress(pending)
                    pending = []
                if len(keys) >= limit:
                    truncated = True
                    break
        finally:
            client.close()
        if on_progress and pending:
            on_progress(pending)
        return KeyScanResult(keys=keys, truncated=truncated)

    # ------------------------------------------------------------------
    # Live, server-side search for the Data Explorer's Search tab - only
    # needed when a type filter is active; pattern-only searches are
    # served from the cached scan_keys() result instead (see
    # KeySearchView in data_explorer_page.py).
    # ------------------------------------------------------------------
    def search_keys(
        self,
        datasource: Datasource,
        pattern: str,
        redis_type: Optional[str] = None,
        limit: int = KEY_SCAN_LIMIT,
        batch_size: int = KEY_SCAN_BATCH_SIZE,
    ) -> KeyScanResult:
        """SCAN MATCH `pattern` [TYPE `redis_type`] - both are applied
        server-side per batch (Redis 6+), so no non-matching key or value
        is ever sent back for a type it isn't. Note this still walks the
        whole keyspace under the hood (SCAN has no prefix/type index to
        skip via), so it costs the same as scan_keys(), just filtered."""
        client = self._make_client(datasource, decode_responses=True)
        keys: List[str] = []
        truncated = False
        try:
            for key in client.scan_iter(match=pattern, count=batch_size, _type=redis_type):
                keys.append(key)
                if len(keys) >= limit:
                    truncated = True
                    break
        finally:
            client.close()
        return KeyScanResult(keys=keys, truncated=truncated)

    # ------------------------------------------------------------------
    # Bulk key deletion for the Data Explorer's Tree tab.
    # ------------------------------------------------------------------
    def delete_keys_by_pattern(
        self, datasource: Datasource, pattern: str, batch_size: int = KEY_SCAN_BATCH_SIZE
    ) -> int:
        """Delete every key currently matching `pattern` (a Redis glob,
        e.g. "foo:bar:*") via SCAN MATCH + UNLINK in batches - never KEYS
        (blocks the server) or FLUSHDB, and never limited to whatever the
        Tree tab happened to already have scanned/cached, so this also
        catches keys created since. Returns how many keys were deleted."""
        client = self._make_client(datasource, decode_responses=True)
        deleted = 0
        try:
            batch: List[str] = []
            for key in client.scan_iter(match=pattern, count=batch_size):
                batch.append(key)
                if len(batch) >= batch_size:
                    deleted += client.unlink(*batch)
                    batch = []
            if batch:
                deleted += client.unlink(*batch)
        finally:
            client.close()
        return deleted

    def delete_keys(
        self, datasource: Datasource, keys: List[str], batch_size: int = KEY_SCAN_BATCH_SIZE
    ) -> int:
        """UNLINK an explicit list of key names, in batches - for the rare
        case a single Redis glob can't express what to delete (e.g. the
        Tree tab's synthetic "no prefix" bucket, whose keys share nothing
        in common beyond having no ":" in their name). Returns how many
        keys were deleted."""
        if not keys:
            return 0
        client = self._make_client(datasource, decode_responses=True)
        deleted = 0
        try:
            for start in range(0, len(keys), batch_size):
                batch = keys[start : start + batch_size]
                deleted += client.unlink(*batch)
        finally:
            client.close()
        return deleted

    # ------------------------------------------------------------------
    # Single-key details for the Key Details view.
    # ------------------------------------------------------------------
    def get_key_details(self, datasource: Datasource, key: str) -> KeyDetails:
        """Fetch everything the Key Details view shows for `key`: type,
        TTL, encoding, memory footprint, idle time, and the value itself
        (rendered as text - see redis_value_format.build_value_text for how
        binary values, e.g. vectors, are handled). Hash and RedisJSON
        ("ReJSON-RL") keys additionally get a structured value
        (hash_fields/json_value) for KeyDetailsDialog's Table/Json tabs -
        see fetch_hash_fields/fetch_json_value. Redis has no notion of a
        key's creation time, so that's not included - OBJECT IDLETIME
        (seconds since last access) is the closest available proxy."""
        client = self._make_client(datasource, decode_responses=False)
        try:
            redis_type = client.type(key)
            redis_type = redis_type.decode() if isinstance(redis_type, bytes) else redis_type
            if redis_type == "none":
                return KeyDetails(
                    key=key,
                    exists=False,
                    type="none",
                    ttl_seconds=None,
                    encoding=None,
                    memory_bytes=None,
                    idle_seconds=None,
                    value_text="",
                    value_truncated=False,
                )

            ttl = client.ttl(key)
            ttl_seconds = ttl if ttl is not None and ttl >= 0 else None

            try:
                encoding = client.object("encoding", key)
                encoding = encoding.decode() if isinstance(encoding, bytes) else encoding
            except redis.ResponseError:
                encoding = None

            try:
                memory_bytes = client.memory_usage(key)
            except redis.ResponseError:
                memory_bytes = None

            try:
                idle_seconds = client.object("idletime", key)
            except redis.ResponseError:
                idle_seconds = None

            hash_fields = None
            json_value = None
            if redis_type == "hash":
                hash_fields, value_truncated = fetch_hash_fields(client, key)
                value_text = "\n".join(f"{field}: {value}" for field, value in hash_fields)
            elif redis_type == "ReJSON-RL":
                try:
                    json_value, value_text, value_truncated = fetch_json_value(client, key)
                except redis.ResponseError as exc:
                    value_text, value_truncated = f"(Could not fetch JSON value: {exc})", False
            else:
                value_text, value_truncated = build_value_text(client, key, redis_type)

            return KeyDetails(
                key=key,
                exists=True,
                type=redis_type,
                ttl_seconds=ttl_seconds,
                encoding=encoding,
                memory_bytes=memory_bytes,
                idle_seconds=idle_seconds,
                value_text=value_text,
                value_truncated=value_truncated,
                hash_fields=hash_fields,
                json_value=json_value,
            )
        finally:
            client.close()

    # Commands whose result is unambiguously a flat list of real key names
    # (as opposed to e.g. SMEMBERS/HKEYS/LRANGE, whose list entries aren't
    # guaranteed to be actual top-level keys) - these get a "browse as a
    # key table" treatment in the Scripts tab instead of plain text.
    KEY_LISTING_COMMANDS = {"KEYS"}

    # ------------------------------------------------------------------
    # Raw command execution for the Scripts tab.
    # ------------------------------------------------------------------
    def execute_script(self, datasource: Datasource, text: str) -> List[CommandExecutionResult]:
        """Run every command in `text` (one per non-blank, non-comment
        line - see redis_command_parser.parse_commands) on a single shared
        connection, in order, so stateful sequences (SELECT, MULTI/EXEC,
        ...) behave the way they would in redis-cli. A command that errors
        is recorded inline and doesn't stop the remaining lines from
        running - mirrors non-interactive redis-cli script execution."""
        commands = parse_commands(text)
        if not commands:
            return []
        client = self._make_client(datasource, decode_responses=False)
        results = []
        try:
            for command in commands:
                keys = None
                try:
                    value = client.execute_command(*command.args)
                    output_text = format_command_result(value)
                    is_error = False
                    if command.args[0].upper() in self.KEY_LISTING_COMMANDS and isinstance(
                        value, (list, tuple, set)
                    ):
                        keys = sorted(v.decode() if isinstance(v, bytes) else v for v in value)
                except redis.RedisError as exc:
                    output_text = str(exc)
                    is_error = True
                results.append(
                    CommandExecutionResult(
                        line_number=command.line_number,
                        command_text=command.raw_text,
                        output_text=output_text,
                        is_error=is_error,
                        keys=keys,
                    )
                )
        finally:
            client.close()
        return results

    # ------------------------------------------------------------------
    # Server diagnostics for the Data Explorer's Stats tab.
    # ------------------------------------------------------------------
    def get_server_info(self, datasource: Datasource) -> dict:
        """A single INFO everything call, covering server/memory/cpu/
        clients/persistence/stats/replication/keyspace/commandstats/
        errorstats/latencystats/cluster/modules in one round-trip.
        Returned as redis-py's already-parsed flat dict rather than a
        dataclass: unlike KeyDetails/IndexDetails this response's shape
        is inherently dynamic - which top-level keys are present depends
        on the Redis version, which modules are loaded, how many
        databases/replicas/distinct commands exist - so the view picks
        out and formats what it needs rather than a fixed schema trying
        to model all of that up front."""
        client = self._make_client(datasource, decode_responses=True)
        try:
            return client.info("everything")
        finally:
            client.close()

    # ------------------------------------------------------------------
    # RediSearch index discovery for the Data Explorer's Indexes tab.
    # ------------------------------------------------------------------
    def list_indexes(self, datasource: Datasource) -> List[str]:
        """FT._LIST - names of every RediSearch index on the server. This
        is intentionally cheap (no per-index FT.INFO round-trip); details
        are fetched lazily by get_index_details when an index is opened -
        mirrors the scan_keys/get_key_details split above. Raises
        redis.ResponseError if the server has no Search module loaded."""
        client = self._make_client(datasource, decode_responses=True)
        try:
            names = client.execute_command("FT._LIST")
        finally:
            client.close()
        return sorted(names)

    def get_index_details(self, datasource: Datasource, index_name: str) -> IndexDetails:
        """FT.INFO <index_name>, parsed into IndexDetails."""
        client = self._make_client(datasource, decode_responses=True)
        try:
            info = client.ft(index_name).info()
        finally:
            client.close()
        return self._parse_index_info(index_name, info)

    def delete_index(self, datasource: Datasource, index_name: str, delete_data: bool) -> None:
        """FT.DROPINDEX <index_name> [DD]. With delete_data=False this only
        detaches the index definition - every hash/JSON document it
        covered is left in place, just no longer searchable. With
        delete_data=True (the DD flag) those documents are deleted too."""
        client = self._make_client(datasource, decode_responses=True)
        try:
            client.ft(index_name).dropindex(delete_documents=delete_data)
        finally:
            client.close()

    # FT.INFO's flat attribute list alternates key/value pairs (identifier,
    # attribute, type, WEIGHT, SEPARATOR, PHONETIC, and per-type config like
    # VECTOR's algorithm/data_type/dim/distance_metric/...) with a handful
    # of bare flag tokens that take no value. The set of possible per-type
    # config keys grows with new field types/algorithms, so parsing assumes
    # "this token starts a key-value pair" by default and only special-cases
    # the small, stable set of true bare flags - the inverse (assume "flag"
    # by default) would silently misparse any config key this list doesn't
    # yet know about, e.g. future VECTOR algorithm parameters.
    _INDEX_ATTR_BARE_FLAGS = {
        "SORTABLE",
        "NOSTEM",
        "NOINDEX",
        "CASESENSITIVE",
        "UNF",
        "INDEXMISSING",
        "INDEXEMPTY",
        "WITHSUFFIXTRIE",
    }

    @classmethod
    def _parse_index_info(cls, index_name: str, info: dict) -> IndexDetails:
        """Parse the dict returned by redis-py's `.ft(name).info()`. Handles
        both shapes redis-py can hand back depending on version/protocol:
        `attributes`/`index_definition` entries as flat key-value-flag
        lists (the classic RESP2 wire shape), or already normalised into
        dicts - see redis-py's commands/search/commands.py for the exact
        variants this is defending against."""

        def as_dict(value) -> dict:
            if isinstance(value, dict):
                return value
            if isinstance(value, (list, tuple)):
                return dict(zip(value[::2], value[1::2]))
            return {}

        def parse_field(raw) -> IndexField:
            if isinstance(raw, dict):
                extra = {
                    k: v for k, v in raw.items() if k not in ("identifier", "attribute", "type", "flags")
                }
                return IndexField(
                    identifier=raw.get("identifier", ""),
                    attribute=raw.get("attribute", raw.get("identifier", "")),
                    type=raw.get("type", "?"),
                    flags=list(raw.get("flags") or []),
                    extra=extra,
                )
            pairs, flags = {}, []
            tokens = list(raw)
            i = 0
            while i < len(tokens):
                token = tokens[i]
                if token not in cls._INDEX_ATTR_BARE_FLAGS and i + 1 < len(tokens):
                    pairs[token] = tokens[i + 1]
                    i += 2
                else:
                    flags.append(token)
                    i += 1
            extra = {k: v for k, v in pairs.items() if k not in ("identifier", "attribute", "type")}
            return IndexField(
                identifier=pairs.get("identifier", ""),
                attribute=pairs.get("attribute", pairs.get("identifier", "")),
                type=pairs.get("type", "?"),
                flags=flags,
                extra=extra,
            )

        index_definition = as_dict(info.get("index_definition"))
        prefixes = index_definition.get("prefixes") or []
        if isinstance(prefixes, str):
            prefixes = [prefixes]

        raw_fields = info.get("attributes") or []
        fields = [parse_field(raw) for raw in raw_fields]

        def to_int(value, default=0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def to_float(value) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return IndexDetails(
            name=info.get("index_name", index_name),
            key_type=index_definition.get("key_type"),
            prefixes=list(prefixes),
            fields=fields,
            num_docs=to_int(info.get("num_docs")),
            num_records=to_int(info.get("num_records")),
            indexing=str(info.get("indexing", "0")) not in ("0", "", "false", "False"),
            percent_indexed=to_float(info.get("percent_indexed")),
            hash_indexing_failures=to_int(info.get("hash_indexing_failures")),
        )

    def _make_client(self, datasource: Datasource, decode_responses: bool = False) -> redis.Redis:
        return redis.Redis(connection_pool=self._get_pool(datasource, decode_responses))

    def _get_pool(self, datasource: Datasource, decode_responses: bool) -> redis.ConnectionPool:
        """Return the cached ConnectionPool for this datasource + decode
        mode, creating it on first use. redis-py bakes decode_responses
        into a pool's connection kwargs (it's ignored on Redis() when
        connection_pool= is passed), so True/False need separate pools."""
        key = (datasource.id, decode_responses)
        with self._pools_lock:
            pool = self._pools.get(key)
            if pool is None:
                pool = redis.ConnectionPool(
                    host=datasource.redis_host,
                    port=datasource.redis_port,
                    username=datasource.redis_user or None,
                    password=datasource.redis_password or None,
                    socket_connect_timeout=CONNECTION_TIMEOUT_SECONDS,
                    socket_timeout=CONNECTION_TIMEOUT_SECONDS,
                    decode_responses=decode_responses,
                )
                self._pools[key] = pool
            return pool

    def _invalidate_pool(self, datasource_id: int) -> None:
        """Drop and disconnect any cached pool(s) for this datasource id -
        called from update()/delete() so an edited host/port/credentials
        (or a deleted datasource) can't leave a pool serving stale
        connection params or dangling sockets."""
        with self._pools_lock:
            pools = [
                self._pools.pop(key)
                for key in list(self._pools)
                if key[0] == datasource_id
            ]
        for pool in pools:
            pool.disconnect()

    def close_all_pools(self) -> None:
        """Disconnect every cached connection pool - call once on app
        shutdown to close sockets cleanly instead of leaving that to the
        OS/GC on process exit."""
        with self._pools_lock:
            pools, self._pools = list(self._pools.values()), {}
        for pool in pools:
            pool.disconnect()


class ProfileRepository:
    """CRUD for `profiles` (pure SQL against SQLite). Deleting a profile
    cascades to its datasources (see the profile_id FK in datasources)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, name: str) -> Profile:
        cursor = self._conn.execute("INSERT INTO profiles (name) VALUES (?)", (name,))
        self._conn.commit()
        return self.get(cursor.lastrowid)

    def list(self) -> List[Profile]:
        rows = self._conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()
        return [self._row_to_profile(row) for row in rows]

    def get(self, profile_id: int) -> Optional[Profile]:
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return self._row_to_profile(row) if row else None

    def update(self, profile: Profile) -> Profile:
        self._conn.execute(
            "UPDATE profiles SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (profile.name, profile.id),
        )
        self._conn.commit()
        return self.get(profile.id)

    def delete(self, profile_id: int) -> None:
        self._conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite), used to remember
    which profile was last active, which datasource was last opened in the
    Data Explorer, and whether the sidebar was collapsed, so all three can
    be restored on startup."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: Optional[str]) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_current_profile_id(self) -> Optional[int]:
        value = self.get(CURRENT_PROFILE_SETTING_KEY)
        return int(value) if value is not None else None

    def set_current_profile_id(self, profile_id: Optional[int]) -> None:
        self.set(CURRENT_PROFILE_SETTING_KEY, str(profile_id) if profile_id is not None else None)

    def get_last_datasource_id(self) -> Optional[int]:
        value = self.get(LAST_DATASOURCE_SETTING_KEY)
        return int(value) if value is not None else None

    def set_last_datasource_id(self, datasource_id: Optional[int]) -> None:
        self.set(LAST_DATASOURCE_SETTING_KEY, str(datasource_id) if datasource_id is not None else None)

    def get_sidebar_collapsed(self) -> bool:
        return self.get(SIDEBAR_COLLAPSED_SETTING_KEY) == "1"

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.set(SIDEBAR_COLLAPSED_SETTING_KEY, "1" if collapsed else "0")


class ScriptRepository:
    """CRUD for `scripts` (pure SQL against SQLite), scoped to a
    datasource. A script is just a name plus raw redis-cli-style command
    text - running it is DatasourceRepository.execute_script's job."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, script: Script) -> Script:
        cursor = self._conn.execute(
            "INSERT INTO scripts (name, datasource_id, text) VALUES (?, ?, ?)",
            (script.name, script.datasource_id, script.text),
        )
        self._conn.commit()
        script.id = cursor.lastrowid
        return script

    def list(self, datasource_id: int) -> List[Script]:
        rows = self._conn.execute(
            "SELECT * FROM scripts WHERE datasource_id = ? ORDER BY name", (datasource_id,)
        ).fetchall()
        return [self._row_to_script(row) for row in rows]

    def get(self, script_id: int) -> Optional[Script]:
        row = self._conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        return self._row_to_script(row) if row else None

    def update(self, script: Script) -> Script:
        self._conn.execute(
            "UPDATE scripts SET name = ?, text = ? WHERE id = ?",
            (script.name, script.text, script.id),
        )
        self._conn.commit()
        return script

    def delete(self, script_id: int) -> None:
        self._conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_script(row: sqlite3.Row) -> Script:
        return Script(
            id=row["id"],
            name=row["name"],
            datasource_id=row["datasource_id"],
            text=row["text"],
        )
