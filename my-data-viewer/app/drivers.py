import contextlib
import logging
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Union

import duckdb
import sqlalchemy
import sshtunnel

from .models import ColumnInfo, Datasource, IndexInfo, QueryResult

# Diagnostics for server-based drivers (SSH tunnel setup, engine connects) -
# these can hang or fail in ways that are otherwise invisible (see
# SshTunnelConfig.open), so this prints progress/errors to stdout rather than
# leaving the caller to wonder whether the app is frozen or just slow.
logger = logging.getLogger("mydataviewer.drivers")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)

DEFAULT_SSH_CONNECT_TIMEOUT = 20.0  # seconds


def _redact(target: Union[str, sqlalchemy.engine.URL]) -> str:
    return sqlalchemy.engine.make_url(target).render_as_string(hide_password=True)


def get_driver(
    datasource: Datasource, column_types: Optional[Dict[str, str]] = None
) -> Union["CsvDriver", "JsonDriver", "PostgresDriver"]:
    """Return the driver object able to run operations against `datasource`.
    `column_types` (name -> DuckDB type) overrides auto-detection for csv -
    see DatasourceRepository, which loads them from `datasources_fields`."""
    if datasource.type == "csv":
        return CsvDriver(datasource.file_path, column_types=column_types)
    if datasource.type == "json":
        return JsonDriver(datasource.file_path, column_types=column_types)
    if datasource.type == "postgres":
        return PostgresDriver(datasource)
    if datasource.type == "mysql":
        raise NotImplementedError("MySQL data sources are not implemented yet.")
    raise ValueError(f"Unknown datasource type: {datasource.type!r}")


@dataclass
class SshTunnelConfig:
    """Bastion/jump-host connection details for reaching a server-based
    datasource that sits in a private network (e.g. a VPC) - equivalent to
    running `ssh -i ssh_key_path -L <local>:<remote_host>:<remote_port>
    ssh_user@ssh_host` yourself. `remote_host`/`remote_port` (the address as
    seen *from* ssh_host) are not stored here - they're the datasource's own
    db_host/db_port (or the host/port embedded in its connection URL),
    supplied when the tunnel is opened."""

    ssh_host: str
    ssh_port: int
    ssh_user: str
    ssh_key_path: str
    ssh_key_passphrase: Optional[str] = None
    connect_timeout: float = DEFAULT_SSH_CONNECT_TIMEOUT

    @contextlib.contextmanager
    def open(self, remote_host: Optional[str], remote_port: Optional[int]) -> Iterator[int]:
        if not remote_host or not remote_port:
            raise ValueError(
                "The SSH tunnel needs a target host and port to forward to - set the "
                "datasource's host/port (or include them in its connection URL)."
            )
        logger.info(
            "Opening SSH tunnel: %s@%s:%s -> %s:%s (key=%s)",
            self.ssh_user, self.ssh_host, self.ssh_port, remote_host, remote_port, self.ssh_key_path,
        )
        forwarder = sshtunnel.SSHTunnelForwarder(
            (self.ssh_host, self.ssh_port),
            ssh_username=self.ssh_user,
            ssh_pkey=self.ssh_key_path,
            ssh_private_key_password=self.ssh_key_passphrase or None,
            remote_bind_address=(remote_host, remote_port),
            # Without these, sshtunnel also tries the SSH agent and every key
            # under ~/.ssh (e.g. id_rsa) in addition to ssh_key_path - fine
            # for a personal `ssh` invocation, wrong for a datasource that
            # names one specific key.
            allow_agent=False,
            host_pkey_directories=[],
            logger=logger,
        )

        # sshtunnel/paramiko open the initial TCP connection to ssh_host with
        # no timeout at all (a plain blocking socket.connect()) - if the host
        # is unreachable (wrong address, or a security group/firewall that
        # silently drops the packets instead of rejecting them), start()
        # can hang for minutes with nothing printed. Run it in a thread and
        # bound how long we wait; a background thread can't be cancelled
        # once inside that blocking call, so a timeout leaves it running
        # (daemonized, so it can't block app exit) rather than truly killing it.
        start_error: List[BaseException] = []

        def _start() -> None:
            try:
                forwarder.start()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
                start_error.append(exc)

        thread = threading.Thread(target=_start, name="ssh-tunnel-connect", daemon=True)
        thread.start()
        thread.join(self.connect_timeout)

        if thread.is_alive():
            logger.error(
                "Timed out after %.0fs connecting to SSH host %s:%s - it may be unreachable "
                "(wrong host/port, or a security group/firewall silently dropping the connection).",
                self.connect_timeout, self.ssh_host, self.ssh_port,
            )
            raise TimeoutError(
                f"Timed out after {self.connect_timeout:.0f}s connecting to SSH host "
                f"{self.ssh_host}:{self.ssh_port}. Check the host/port and that this "
                f"machine can reach it on that port (security group/firewall rules)."
            )
        if start_error:
            logger.error("SSH tunnel setup failed: %s", start_error[0])
            raise start_error[0]
        if not forwarder.is_active:
            raise RuntimeError(f"Could not establish the SSH tunnel to {self.ssh_host}:{self.ssh_port}.")

        logger.info(
            "SSH tunnel up: 127.0.0.1:%s -> %s:%s (via %s)",
            forwarder.local_bind_port, remote_host, remote_port, self.ssh_host,
        )
        try:
            yield forwarder.local_bind_port
        finally:
            logger.info("Closing SSH tunnel to %s", self.ssh_host)
            forwarder.stop()


def _ssh_tunnel_for(datasource: Datasource) -> Optional[SshTunnelConfig]:
    if not datasource.ssh_tunnel_enabled:
        return None
    if not datasource.ssh_host or not datasource.ssh_user or not datasource.ssh_key_path:
        raise ValueError("An SSH tunnel requires an SSH host, user, and private key file.")
    return SshTunnelConfig(
        ssh_host=datasource.ssh_host,
        ssh_port=datasource.ssh_port or 22,
        ssh_user=datasource.ssh_user,
        ssh_key_path=datasource.ssh_key_path,
        ssh_key_passphrase=datasource.ssh_key_passphrase,
    )


def _table_name_for(file_path: str) -> str:
    """Turn a CSV file's name into a usable SQL identifier."""
    stem = Path(file_path).stem
    name = re.sub(r"\W", "_", stem)
    if not name or name[0].isdigit():
        name = f"t_{name}"
    return name


class CsvDriver:
    """Exposes a single CSV file as one queryable SQL table, via DuckDB.

    Each call opens its own in-memory DuckDB connection and registers the
    CSV as a view named after the file - DuckDB reads the file directly
    (read_csv_auto), so this works without loading the whole file into
    Python memory first. `column_types` (name -> DuckDB type), when given,
    is passed through as the view's dtype override instead of letting
    DuckDB auto-detect every column.
    """

    def __init__(self, file_path: str, column_types: Optional[Dict[str, str]] = None):
        self.file_path = file_path
        self.table_name = _table_name_for(file_path)
        self.column_types = column_types or {}

    def list_tables(self) -> List[str]:
        return [self.table_name]

    def list_columns(self, table: str) -> List[ColumnInfo]:
        con = self._connect()
        try:
            description = con.execute(f'SELECT * FROM "{self.table_name}" LIMIT 0').description
            return [ColumnInfo(name=col[0], type=str(col[1])) for col in description]
        finally:
            con.close()

    def list_indexes(self, table: str) -> List[IndexInfo]:
        return []  # a CSV file has no indexes

    def test_connection(self) -> None:
        """Raises if the CSV can't be opened/parsed by DuckDB."""
        con = self._connect()
        con.close()

    def execute_sql(self, sql: str, params: Optional[Sequence[object]] = None) -> QueryResult:
        con = self._connect()
        try:
            cursor = con.execute(sql, params or [])
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return QueryResult(columns=columns, rows=rows)
        finally:
            con.close()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        # Registers the CSV as a view via DuckDB's relation API rather than
        # interpolating the file path into a SQL string.
        con.read_csv(self.file_path, dtype=self.column_types).create_view(self.table_name)
        return con


class JsonDriver:
    """Exposes a single JSON file as one queryable SQL table, via DuckDB's
    JSON reader - handles a top-level array of objects and newline-delimited
    JSON (ndjson) alike, since DuckDB auto-detects the layout (`format="auto"`
    by default). Mirrors CsvDriver: each call opens its own in-memory DuckDB
    connection and registers the file as a view via the relation API rather
    than interpolating the file path into a SQL string. `column_types` (name
    -> DuckDB type), when given, is passed through as the view's column-type
    override instead of letting DuckDB auto-detect every column.
    """

    def __init__(self, file_path: str, column_types: Optional[Dict[str, str]] = None):
        self.file_path = file_path
        self.table_name = _table_name_for(file_path)
        self.column_types = column_types or {}

    def list_tables(self) -> List[str]:
        return [self.table_name]

    def list_columns(self, table: str) -> List[ColumnInfo]:
        con = self._connect()
        try:
            description = con.execute(f'SELECT * FROM "{self.table_name}" LIMIT 0').description
            return [ColumnInfo(name=col[0], type=str(col[1])) for col in description]
        finally:
            con.close()

    def list_indexes(self, table: str) -> List[IndexInfo]:
        return []  # a JSON file has no indexes

    def test_connection(self) -> None:
        """Raises if the JSON file can't be opened/parsed by DuckDB."""
        con = self._connect()
        con.close()

    def execute_sql(self, sql: str, params: Optional[Sequence[object]] = None) -> QueryResult:
        con = self._connect()
        try:
            cursor = con.execute(sql, params or [])
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return QueryResult(columns=columns, rows=rows)
        finally:
            con.close()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        con.read_json(self.file_path, columns=self.column_types or None).create_view(self.table_name)
        return con


class SqlAlchemyDriver:
    """Base facade over a real SQL database, via SQLAlchemy - so adding
    another server-based driver (mysql, ...) means subclassing this and
    supplying a connection target, not re-implementing table/column/index
    introspection or query execution.

    Each call builds and disposes its own Engine (matching CsvDriver's
    open-per-call style) rather than keeping a pooled connection alive
    across calls. When `ssh_tunnel` is given, each call also opens (and
    tears back down) its own SSH tunnel around that Engine, forwarding to
    the host/port already embedded in `target` - see `_connect()`.
    """

    def __init__(
        self,
        target: Union[str, sqlalchemy.engine.URL],
        ssh_tunnel: Optional[SshTunnelConfig] = None,
    ):
        self.target = target
        self.ssh_tunnel = ssh_tunnel

    def list_tables(self) -> List[str]:
        with self._connect() as engine:
            inspector = sqlalchemy.inspect(engine)
            names = set(inspector.get_table_names()) | set(inspector.get_view_names())
            return sorted(names)

    def list_columns(self, table: str) -> List[ColumnInfo]:
        with self._connect() as engine:
            inspector = sqlalchemy.inspect(engine)
            pk_columns = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
            columns = []
            for col in inspector.get_columns(table):
                constraints = []
                if col["name"] in pk_columns:
                    constraints.append("PRIMARY KEY")
                if not col.get("nullable", True):
                    constraints.append("NOT NULL")
                columns.append(
                    ColumnInfo(name=col["name"], type=str(col["type"]), constraints=", ".join(constraints))
                )
            return columns

    def list_indexes(self, table: str) -> List[IndexInfo]:
        with self._connect() as engine:
            inspector = sqlalchemy.inspect(engine)
            return [
                IndexInfo(name=index["name"], columns=list(index["column_names"]))
                for index in inspector.get_indexes(table)
            ]

    def test_connection(self) -> None:
        with self._connect() as engine:
            with engine.connect():
                pass

    def execute_sql(self, sql: str, params: Optional[Sequence[object]] = None) -> QueryResult:
        with self._connect() as engine:
            sql = self._translate_placeholders(engine, sql)
            raw_conn = engine.raw_connection()
            try:
                cursor = raw_conn.cursor()
                try:
                    cursor.execute(sql, list(params or []))
                    columns = [col[0] for col in cursor.description] if cursor.description else []
                    rows = [tuple(row) for row in cursor.fetchall()]
                    return QueryResult(columns=columns, rows=rows)
                finally:
                    cursor.close()
            finally:
                raw_conn.close()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlalchemy.engine.Engine]:
        """Yield a live Engine, routed through the SSH tunnel when one is
        configured - the tunnel forwards to whatever host/port `target`
        already points at, and the Engine is rebuilt to hit `127.0.0.1` on
        the resulting local port instead."""
        if self.ssh_tunnel is None:
            logger.info("Connecting directly to %s", _redact(self.target))
            engine = sqlalchemy.create_engine(self.target)
            try:
                yield engine
            except Exception:
                logger.exception("Database operation against %s failed", _redact(self.target))
                raise
            finally:
                engine.dispose()
            return

        url = sqlalchemy.engine.make_url(self.target)
        with self.ssh_tunnel.open(url.host, url.port) as local_port:
            engine = sqlalchemy.create_engine(url.set(host="127.0.0.1", port=local_port))
            try:
                yield engine
            except Exception:
                logger.exception("Database operation against %s (via SSH tunnel) failed", _redact(self.target))
                raise
            finally:
                engine.dispose()

    @staticmethod
    def _translate_placeholders(engine: sqlalchemy.engine.Engine, sql: str) -> str:
        """Callers build SQL with '?' placeholders (DB-API "qmark" style, as
        used by sqlite3/duckdb) - translate to whatever paramstyle the
        underlying DBAPI driver actually expects."""
        paramstyle = engine.dialect.paramstyle
        if paramstyle == "qmark":
            return sql
        if paramstyle in ("format", "pyformat"):
            return sql.replace("?", "%s")
        raise NotImplementedError(f"Unsupported DBAPI paramstyle: {paramstyle!r}")


class PostgresDriver(SqlAlchemyDriver):
    """Postgres via SQLAlchemy + psycopg2. Accepts either a full connection
    URL (`datasource.url`, e.g. postgresql://user:pass@host/dbname) or the
    separate host/port/name/user/password fields - the URL wins if both are
    set."""

    def __init__(self, datasource: Datasource):
        super().__init__(_postgres_connection_target(datasource), ssh_tunnel=_ssh_tunnel_for(datasource))


def _postgres_connection_target(datasource: Datasource) -> Union[str, sqlalchemy.engine.URL]:
    if datasource.url:
        return datasource.url
    if not datasource.db_host or not datasource.db_name:
        raise ValueError(
            "Postgres datasource requires either a connection URL or a host and database name."
        )
    return sqlalchemy.engine.URL.create(
        drivername="postgresql+psycopg2",
        username=datasource.db_user or None,
        password=datasource.db_password or None,
        host=datasource.db_host,
        port=datasource.db_port,
        database=datasource.db_name,
    )


def infer_csv_columns(file_path: str) -> List[ColumnInfo]:
    """Sniff a CSV's column names/types via DuckDB, without registering a
    view - used by the "Infer types" button in the datasource dialog, before
    the datasource (and its file path) has necessarily been saved anywhere."""
    con = duckdb.connect(":memory:")
    try:
        relation = con.read_csv(file_path)
        return [
            ColumnInfo(name=name, type=str(dtype))
            for name, dtype in zip(relation.columns, relation.types)
        ]
    finally:
        con.close()


def infer_json_columns(file_path: str) -> List[ColumnInfo]:
    """Sniff a JSON file's column names/types via DuckDB (array-of-objects or
    ndjson alike), without registering a view - used by the "Infer types"
    button in the datasource dialog, before the datasource (and its file
    path) has necessarily been saved anywhere."""
    con = duckdb.connect(":memory:")
    try:
        relation = con.read_json(file_path)
        return [
            ColumnInfo(name=name, type=str(dtype))
            for name, dtype in zip(relation.columns, relation.types)
        ]
    finally:
        con.close()
