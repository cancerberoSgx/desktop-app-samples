import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Union

import duckdb
import sqlalchemy

from .models import ColumnInfo, Datasource, IndexInfo, QueryResult

if TYPE_CHECKING:
    from .async_tasks import TaskHandle

# Rows are pulled in chunks rather than via one cursor.fetchall() call. For a
# big result set, fetchall() converts every row to a Python object inside a
# single uninterrupted C call - since that's pure GIL-held object
# construction (no network wait to release it around), it can lock out the
# UI thread's event loop for the whole call even though the query itself
# runs on a background thread. Fetching in batches gives the interpreter a
# checkpoint between chunks to hand the GIL back, and doubles as a
# cancellation point for a single very large query.
_FETCH_BATCH_SIZE = 2000


def _fetch_rows(cursor, handle: Optional["TaskHandle"]) -> List[tuple]:
    rows: List[tuple] = []
    while handle is None or not handle.is_cancelled():
        batch = cursor.fetchmany(_FETCH_BATCH_SIZE)
        if not batch:
            break
        rows.extend(tuple(row) for row in batch)
    return rows


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

    def execute_sql(
        self,
        sql: str,
        params: Optional[Sequence[object]] = None,
        handle: Optional["TaskHandle"] = None,
    ) -> QueryResult:
        con = self._connect()
        if handle is not None:
            # Best-effort cancellation: DuckDB's interrupt() is safe to call
            # from another thread while execute() is blocked on this one.
            handle.set_interrupt(con.interrupt)
        try:
            cursor = con.execute(sql, params or [])
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = _fetch_rows(cursor, handle)
            return QueryResult(columns=columns, rows=rows)
        finally:
            if handle is not None:
                handle.clear_interrupt()
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

    def execute_sql(
        self,
        sql: str,
        params: Optional[Sequence[object]] = None,
        handle: Optional["TaskHandle"] = None,
    ) -> QueryResult:
        con = self._connect()
        if handle is not None:
            handle.set_interrupt(con.interrupt)
        try:
            cursor = con.execute(sql, params or [])
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = _fetch_rows(cursor, handle)
            return QueryResult(columns=columns, rows=rows)
        finally:
            if handle is not None:
                handle.clear_interrupt()
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
    across calls.
    """

    def __init__(self, target: Union[str, sqlalchemy.engine.URL]):
        self.target = target

    def list_tables(self) -> List[str]:
        engine = self._make_engine()
        try:
            inspector = sqlalchemy.inspect(engine)
            names = set(inspector.get_table_names()) | set(inspector.get_view_names())
            return sorted(names)
        finally:
            engine.dispose()

    def list_columns(self, table: str) -> List[ColumnInfo]:
        engine = self._make_engine()
        try:
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
        finally:
            engine.dispose()

    def list_indexes(self, table: str) -> List[IndexInfo]:
        engine = self._make_engine()
        try:
            inspector = sqlalchemy.inspect(engine)
            return [
                IndexInfo(name=index["name"], columns=list(index["column_names"]))
                for index in inspector.get_indexes(table)
            ]
        finally:
            engine.dispose()

    def test_connection(self) -> None:
        engine = self._make_engine()
        try:
            with engine.connect():
                pass
        finally:
            engine.dispose()

    def execute_sql(
        self,
        sql: str,
        params: Optional[Sequence[object]] = None,
        handle: Optional["TaskHandle"] = None,
    ) -> QueryResult:
        engine = self._make_engine()
        try:
            sql = self._translate_placeholders(engine, sql)
            raw_conn = engine.raw_connection()
            # Best-effort cancellation: DBAPI connections (psycopg2 included)
            # that expose cancel() support calling it from another thread
            # while a query is blocked on this one.
            if handle is not None and hasattr(raw_conn, "cancel"):
                handle.set_interrupt(raw_conn.cancel)
            try:
                cursor = raw_conn.cursor()
                try:
                    cursor.execute(sql, list(params or []))
                    columns = [col[0] for col in cursor.description] if cursor.description else []
                    rows = _fetch_rows(cursor, handle)
                    return QueryResult(columns=columns, rows=rows)
                finally:
                    cursor.close()
            finally:
                if handle is not None:
                    handle.clear_interrupt()
                raw_conn.close()
        finally:
            engine.dispose()

    def _make_engine(self) -> sqlalchemy.engine.Engine:
        return sqlalchemy.create_engine(self.target)

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
        super().__init__(_postgres_connection_target(datasource))


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


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def write_rows_to_parquet(
    columns: Sequence[str], rows: Sequence[Sequence[object]], output_path: str
) -> None:
    """Write a plain (columns, rows) result - as returned by any driver's
    `execute_sql` - to a single Parquet file, via a throwaway in-memory
    DuckDB connection. This is the one shared path every datasource type's
    export goes through: csv/json rows already came from DuckDB, and
    postgres/mysql rows are a fetched result set, but by the time they reach
    here it's just column names + row tuples either way, so there's nothing
    type-specific left to do.
    """
    con = duckdb.connect(":memory:")
    try:
        quoted_columns = ", ".join(_quote_ident(c) for c in columns)
        if rows:
            row_placeholders = ", ".join(f"({', '.join(['?'] * len(columns))})" for _ in rows)
            params = [value for row in rows for value in row]
            con.execute(
                f"CREATE TABLE export_data AS SELECT * FROM (VALUES {row_placeholders}) AS t({quoted_columns})",
                params,
            )
        else:
            # No rows to type-infer from - fall back to VARCHAR so the file
            # still has the right column names.
            column_defs = ", ".join(f"{_quote_ident(c)} VARCHAR" for c in columns)
            con.execute(f"CREATE TABLE export_data ({column_defs})")
        con.table("export_data").write_parquet(output_path)
    finally:
        con.close()


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
