import sqlite3
from typing import List, Optional

import redis

from .models import Datasource, Profile

CURRENT_PROFILE_SETTING_KEY = "current_profile_id"
CONNECTION_TIMEOUT_SECONDS = 5


class DatasourceRepository:
    """CRUD for `datasources` (pure SQL against SQLite), scoped to a profile,
    plus `test_connection`, which opens a real connection to the Redis server
    described by the record and issues a PING."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

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
        return datasource

    def delete(self, datasource_id: int) -> None:
        self._conn.execute("DELETE FROM datasources WHERE id = ?", (datasource_id,))
        self._conn.commit()

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
        client = redis.Redis(
            host=datasource.redis_host,
            port=datasource.redis_port,
            username=datasource.redis_user or None,
            password=datasource.redis_password or None,
            socket_connect_timeout=CONNECTION_TIMEOUT_SECONDS,
            socket_timeout=CONNECTION_TIMEOUT_SECONDS,
        )
        try:
            if not client.ping():
                raise ConnectionError("PING did not return a successful response.")
        finally:
            client.close()


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
