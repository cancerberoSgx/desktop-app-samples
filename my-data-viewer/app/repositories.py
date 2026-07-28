import sqlite3
from typing import List, Optional

from . import drivers
from .models import ColumnInfo, Datasource, IndexInfo, QueryResult


class DatasourceRepository:
    """CRUD for `datasources` (pure SQL against SQLite), plus operations
    that run against the data source itself (list tables/columns/indexes,
    execute SQL) delegated to a per-type driver (see drivers.py)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(self, datasource: Datasource) -> Datasource:
        cursor = self._conn.execute(
            """
            INSERT INTO datasources
                (name, type, file_path, db_host, db_port, db_name, db_user, db_password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datasource.name,
                datasource.type,
                datasource.file_path,
                datasource.db_host,
                datasource.db_port,
                datasource.db_name,
                datasource.db_user,
                datasource.db_password,
            ),
        )
        self._conn.commit()
        datasource.id = cursor.lastrowid
        return datasource

    def list(
        self,
        name_contains: Optional[str] = None,
        type_: Optional[str] = None,
    ) -> List[Datasource]:
        query = "SELECT * FROM datasources WHERE 1=1"
        params: list = []
        if name_contains:
            query += " AND name LIKE ?"
            params.append(f"%{name_contains}%")
        if type_:
            query += " AND type = ?"
            params.append(type_)
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
            SET name = ?, type = ?, file_path = ?, db_host = ?, db_port = ?,
                db_name = ?, db_user = ?, db_password = ?
            WHERE id = ?
            """,
            (
                datasource.name,
                datasource.type,
                datasource.file_path,
                datasource.db_host,
                datasource.db_port,
                datasource.db_name,
                datasource.db_user,
                datasource.db_password,
                datasource.id,
            ),
        )
        self._conn.commit()
        return datasource

    def delete(self, datasource_id: int) -> None:
        self._conn.execute("DELETE FROM datasources WHERE id = ?", (datasource_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_datasource(row: sqlite3.Row) -> Datasource:
        return Datasource(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            file_path=row["file_path"],
            db_host=row["db_host"],
            db_port=row["db_port"],
            db_name=row["db_name"],
            db_user=row["db_user"],
            db_password=row["db_password"],
        )

    # ------------------------------------------------------------------
    # Operations against the underlying data source
    # ------------------------------------------------------------------
    def list_tables(self, datasource: Datasource) -> List[str]:
        return drivers.get_driver(datasource).list_tables()

    def list_columns(self, datasource: Datasource, table: str) -> List[ColumnInfo]:
        return drivers.get_driver(datasource).list_columns(table)

    def list_indexes(self, datasource: Datasource, table: str) -> List[IndexInfo]:
        return drivers.get_driver(datasource).list_indexes(table)

    def execute_sql(self, datasource: Datasource, sql: str) -> QueryResult:
        return drivers.get_driver(datasource).execute_sql(sql)
