import json
import subprocess
import sqlite3
from typing import List, Optional

from .models import Container

DOCKER_BINARY = "docker"
COMMAND_TIMEOUT_SECONDS = 15


class DockerNotAvailableError(RuntimeError):
    """Raised when the `docker` executable itself cannot be found on PATH -
    the caller is expected to show a clear "Docker isn't installed" message
    rather than let this surface as an unhandled traceback."""


class DockerCommandError(RuntimeError):
    """Raised when `docker` runs but the invoked command fails (daemon
    unreachable, no such container, permission denied, timed out...);
    message is docker's own stderr wherever available."""


class ContainerRepository:
    """Wraps the docker CLI - no docker SDK dependency, every operation
    shells out to `docker` and parses its `--format '{{json .}}'` output.
    `list()` merges `docker ps -a --size` (identity, status, size) with
    `docker stats --no-stream` (live cpu/mem, running containers only)."""

    def list(self) -> List[Container]:
        containers = {c.id: c for c in self._ps()}
        for container_id, cpu_percent, mem_usage, mem_percent in self._stats():
            container = containers.get(container_id)
            if container is not None:
                container.cpu_percent = cpu_percent
                container.mem_usage = mem_usage
                container.mem_percent = mem_percent
        return sorted(containers.values(), key=lambda c: c.names)

    def stop(self, container_id: str) -> None:
        self._run(["stop", container_id])

    def remove(self, container_id: str, force: bool = False) -> None:
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(container_id)
        self._run(args)

    # ------------------------------------------------------------------
    def _ps(self) -> List[Container]:
        output = self._run(["ps", "-a", "--size", "--format", "{{json .}}"])
        containers = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            containers.append(
                Container(
                    id=data.get("ID", ""),
                    names=data.get("Names", ""),
                    image=data.get("Image", ""),
                    command=data.get("Command", ""),
                    created_at=data.get("CreatedAt", ""),
                    status=data.get("Status", ""),
                    state=data.get("State", ""),
                    size=data.get("Size", ""),
                    ports=data.get("Ports", ""),
                )
            )
        return containers

    def _stats(self):
        # No running containers -> docker still exits 0 with empty stdout.
        output = self._run(["stats", "--no-stream", "--format", "{{json .}}"])
        rows = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            rows.append(
                (data.get("ID", ""), data.get("CPUPerc", ""), data.get("MemUsage", ""), data.get("MemPerc", ""))
            )
        return rows

    @staticmethod
    def _run(args: List[str]) -> str:
        try:
            result = subprocess.run(
                [DOCKER_BINARY, *args],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise DockerNotAvailableError(
                "The 'docker' command was not found on PATH. Install Docker "
                "and make sure it's available before using this app."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerCommandError(
                f"docker {' '.join(args)} timed out after {COMMAND_TIMEOUT_SECONDS}s."
            ) from exc

        if result.returncode != 0:
            raise DockerCommandError(
                result.stderr.strip() or f"docker {' '.join(args)} failed with exit code {result.returncode}."
            )
        return result.stdout


class SettingsRepository:
    """Key/value app settings (pure SQL against SQLite). Not wired into any
    screen yet - added now so future preferences (e.g. remembered filters,
    auto-refresh interval) have a ready-made place to live, following the
    same pattern used in my-redis-viewer."""

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
