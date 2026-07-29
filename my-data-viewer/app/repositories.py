import os
import sqlite3
from typing import List, Optional

from . import drivers
from .models import ColumnInfo, Datasource, DatasourceField, IndexInfo, Profile, QueryResult, Script

CURRENT_PROFILE_SETTING_KEY = "current_profile_id"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class DatasourceRepository:
    """CRUD for `datasources` (pure SQL against SQLite), scoped to a profile,
    plus operations that run against the data source itself (list
    tables/columns/indexes, execute SQL) delegated to a per-type driver
    (see drivers.py)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(self, datasource: Datasource) -> Datasource:
        cursor = self._conn.execute(
            """
            INSERT INTO datasources
                (name, type, profile_id, file_path, url, db_host, db_port, db_name, db_user, db_password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datasource.name,
                datasource.type,
                datasource.profile_id,
                datasource.file_path,
                datasource.url,
                datasource.db_host,
                datasource.db_port,
                datasource.db_name,
                datasource.db_user,
                datasource.db_password,
            ),
        )
        self._conn.commit()
        datasource.id = cursor.lastrowid
        self._save_fields(datasource.id, datasource.fields)
        return datasource

    def list(
        self,
        profile_id: int,
        name_contains: Optional[str] = None,
        type_: Optional[str] = None,
    ) -> List[Datasource]:
        query = "SELECT * FROM datasources WHERE profile_id = ?"
        params: list = [profile_id]
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
            SET name = ?, type = ?, file_path = ?, url = ?, db_host = ?, db_port = ?,
                db_name = ?, db_user = ?, db_password = ?
            WHERE id = ?
            """,
            (
                datasource.name,
                datasource.type,
                datasource.file_path,
                datasource.url,
                datasource.db_host,
                datasource.db_port,
                datasource.db_name,
                datasource.db_user,
                datasource.db_password,
                datasource.id,
            ),
        )
        self._conn.commit()
        self._save_fields(datasource.id, datasource.fields)
        return datasource

    def delete(self, datasource_id: int) -> None:
        self._conn.execute("DELETE FROM datasources WHERE id = ?", (datasource_id,))
        self._conn.commit()

    def set_last_script_id(self, datasource_id: int, script_id: Optional[int]) -> None:
        self._conn.execute(
            "UPDATE datasources SET last_script_id = ? WHERE id = ?", (script_id, datasource_id)
        )
        self._conn.commit()

    @staticmethod
    def _row_to_datasource(row: sqlite3.Row) -> Datasource:
        return Datasource(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            profile_id=row["profile_id"],
            file_path=row["file_path"],
            url=row["url"],
            db_host=row["db_host"],
            db_port=row["db_port"],
            db_name=row["db_name"],
            db_user=row["db_user"],
            db_password=row["db_password"],
            last_script_id=row["last_script_id"],
        )

    # ------------------------------------------------------------------
    # datasources_fields (1-N, csv/json only) - the name/type the user
    # confirmed via "Infer types" in the dialog, reapplied every time the
    # file is queried so DuckDB doesn't have to re-guess (and can't disagree
    # with what the user picked).
    # ------------------------------------------------------------------
    def list_fields(self, datasource_id: int) -> List[DatasourceField]:
        rows = self._conn.execute(
            "SELECT * FROM datasources_fields WHERE datasource_id = ? ORDER BY position",
            (datasource_id,),
        ).fetchall()
        return [self._row_to_field(row) for row in rows]

    def _save_fields(self, datasource_id: int, fields: List[DatasourceField]) -> None:
        self._conn.execute("DELETE FROM datasources_fields WHERE datasource_id = ?", (datasource_id,))
        self._conn.executemany(
            "INSERT INTO datasources_fields (datasource_id, name, type, position) VALUES (?, ?, ?, ?)",
            [(datasource_id, f.name, f.type, position) for position, f in enumerate(fields)],
        )
        self._conn.commit()

    @staticmethod
    def _row_to_field(row: sqlite3.Row) -> DatasourceField:
        return DatasourceField(
            id=row["id"],
            datasource_id=row["datasource_id"],
            name=row["name"],
            type=row["type"],
            position=row["position"],
        )

    # ------------------------------------------------------------------
    # Operations against the underlying data source
    # ------------------------------------------------------------------
    def _driver_for(self, datasource: Datasource):
        column_types = None
        if datasource.type in ("csv", "json") and datasource.id is not None:
            fields = self.list_fields(datasource.id)
            if fields:
                column_types = {f.name: f.type for f in fields}
        return drivers.get_driver(datasource, column_types=column_types)

    def list_tables(self, datasource: Datasource) -> List[str]:
        return self._driver_for(datasource).list_tables()

    def list_columns(self, datasource: Datasource, table: str) -> List[ColumnInfo]:
        return self._driver_for(datasource).list_columns(table)

    def list_indexes(self, datasource: Datasource, table: str) -> List[IndexInfo]:
        return self._driver_for(datasource).list_indexes(table)

    def test_connection(self, datasource: Datasource) -> None:
        self._driver_for(datasource).test_connection()

    def execute_sql(
        self, datasource: Datasource, sql: str, params: Optional[list] = None
    ) -> QueryResult:
        return self._driver_for(datasource).execute_sql(sql, params)

    # ------------------------------------------------------------------
    # Export - same code path regardless of datasource type, since it's
    # built entirely on list_tables/list_columns/execute_sql above.
    # ------------------------------------------------------------------
    def export_to_parquet(self, datasource: Datasource, output_dir: str) -> List[str]:
        """Dump every table in `datasource` into its own '<table>.parquet'
        file inside `output_dir`. Returns the paths written."""
        written = []
        for table in self.list_tables(datasource):
            result = self.execute_sql(datasource, f"SELECT * FROM {_quote_ident(table)}")
            output_path = os.path.join(output_dir, f"{table}.parquet")
            drivers.write_rows_to_parquet(result.columns, result.rows, output_path)
            written.append(output_path)
        return written

    def export_schema_to_parquet(self, datasource: Datasource, output_path: str) -> None:
        """Dump one row per column across every table in `datasource`
        (table_name, column_name, type, constraints) into a single Parquet
        file - schema only, no data."""
        rows = []
        for table in self.list_tables(datasource):
            for column in self.list_columns(datasource, table):
                rows.append((table, column.name, column.type, column.constraints or ""))
        columns = ["table_name", "column_name", "type", "constraints"]
        drivers.write_rows_to_parquet(columns, rows, output_path)


class ScriptRepository:
    """CRUD for `scripts` (pure SQL against SQLite), scoped to a datasource
    (and, via it, a profile) - see DatasourceRepository for the equivalent
    pattern this follows."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, script: Script) -> Script:
        cursor = self._conn.execute(
            "INSERT INTO scripts (profile_id, datasource_id, name, content) VALUES (?, ?, ?, ?)",
            (script.profile_id, script.datasource_id, script.name, script.content),
        )
        self._conn.commit()
        return self.get(cursor.lastrowid)

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
            "UPDATE scripts SET name = ?, content = ?, updated_at = datetime('now') WHERE id = ?",
            (script.name, script.content, script.id),
        )
        self._conn.commit()
        return self.get(script.id)

    def delete(self, script_id: int) -> None:
        self._conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        self._conn.commit()

    @staticmethod
    def _row_to_script(row: sqlite3.Row) -> Script:
        return Script(
            id=row["id"],
            name=row["name"],
            content=row["content"],
            profile_id=row["profile_id"],
            datasource_id=row["datasource_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


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
    which profile was last active so it can be reloaded on startup."""

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
