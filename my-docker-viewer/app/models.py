from dataclasses import dataclass
from typing import Optional


@dataclass
class Container:
    """A single row from `docker ps -a`, merged with its live `docker
    stats` sample when the container is running (cpu/mem fields are None
    for stopped containers, since docker stats only reports on running
    ones)."""

    id: str
    names: str
    image: str
    command: str
    created_at: str
    status: str
    state: str
    size: str = ""
    ports: str = ""
    cpu_percent: Optional[str] = None
    mem_usage: Optional[str] = None
    mem_percent: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.state.lower() in ("running", "restarting")
