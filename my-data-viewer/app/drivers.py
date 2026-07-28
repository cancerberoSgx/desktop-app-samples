import re
from pathlib import Path
from typing import List

import duckdb

from .models import ColumnInfo, Datasource, IndexInfo, QueryResult


def get_driver(datasource: Datasource) -> "CsvDriver":
    """Return the driver object able to run operations against `datasource`."""
    if datasource.type == "csv":
        return CsvDriver(datasource.file_path)
    if datasource.type == "postgres":
        raise NotImplementedError("Postgres data sources are not implemented yet.")
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
    Python memory first.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.table_name = _table_name_for(file_path)

    def list_tables(self) -> List[str]:
        return [self.table_name]

    def list_columns(self, table: str) -> List[ColumnInfo]:
        con = self._connect()
        try:
            description = con.execute(f'SELECT * FROM "{self.table_name}" LIMIT 0').description
            return [ColumnInfo(name=col[0], type="text") for col in description]
        finally:
            con.close()

    def list_indexes(self, table: str) -> List[IndexInfo]:
        return []  # a CSV file has no indexes

    def execute_sql(self, sql: str) -> QueryResult:
        con = self._connect()
        try:
            cursor = con.execute(sql)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return QueryResult(columns=columns, rows=rows)
        finally:
            con.close()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        # Registers the CSV as a view via DuckDB's relation API rather than
        # interpolating the file path into a SQL string.
        con.read_csv(self.file_path).create_view(self.table_name)
        return con
