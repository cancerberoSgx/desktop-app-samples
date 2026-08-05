from dataclasses import dataclass, field
from typing import List, Optional


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
    # Docker's own "N units ago" rendering of created_at (its `RunningFor`
    # field, despite the name it's relative to creation, not last start) -
    # used for display; created_at is kept as docker's raw, lexicographically
    # sortable timestamp string for column sorting.
    created_for: str = ""
    cpu_percent: Optional[str] = None
    mem_usage: Optional[str] = None
    mem_percent: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.state.lower() in ("running", "restarting")


@dataclass
class Image:
    """A single row from `docker image ls` (no `-a` - that flag also lists
    intermediate build-cache layers, which aren't things a user would ever
    remove/prune individually; the plain, no-`-a` list is what `docker
    images` and every other docker tool shows by default). `containers` is
    docker's own count of containers - running or stopped - currently
    referencing this image, read straight off the `Containers` format
    placeholder; no cross-referencing against `docker ps` needed to get it."""

    id: str
    repository: str
    tag: str
    created_at: str
    created_since: str
    size: str = ""
    containers: int = 0

    @property
    def is_dangling(self) -> bool:
        return self.repository == "<none>" or self.tag == "<none>"

    @property
    def reference(self) -> str:
        """What to pass to `docker image rm`, and this row's stable
        identity for re-selecting it after a reload - the ID for a
        dangling image (no meaningful repo:tag to give docker), otherwise
        `repository:tag`."""
        if self.is_dangling:
            return self.id
        return f"{self.repository}:{self.tag}"

    @property
    def status(self) -> str:
        """Client-side classification, not something docker reports
        directly - drives both the Status column and the status filter."""
        if self.is_dangling:
            return "Dangling"
        return "In use" if self.containers > 0 else "Unused"


@dataclass
class Mount:
    """One entry from a container's `docker inspect` Mounts array. `kind` is
    docker's own mount `Type` ("volume", "bind", "tmpfs", ...). `identifier`
    is what you'd pass to `docker run -v <identifier>:...` to reach the same
    data - the volume name for a "volume" mount, the host path (`Source`)
    for a "bind" mount; unused (empty) for kinds we don't size (e.g. tmpfs,
    which is memory-backed and has no disk footprint to measure).
    `shared` is True when some *other* container also mounts this same
    volume/path - freeing it isn't as simple as removing just this one
    container, which matters for a "what can I delete to reclaim space"
    view."""

    kind: str
    identifier: str
    destination: str
    shared: bool = False


@dataclass
class ContainerDiskUsage:
    """Disk usage for one container, computed on demand (see
    `DiskUsageRepository`) rather than loaded eagerly like `Container` -
    identity/mounts are cheap and known up front, but sizing them means
    running `du`, which is comparatively slow. `layer_bytes` and
    `mounts_bytes` are None until computed; `notes` collects
    human-readable caveats (a mount that's shared, unsupported, or could
    no longer be found) without blocking the rest of the total."""

    id: str
    names: str
    image: str
    mounts: List[Mount] = field(default_factory=list)
    layer_bytes: Optional[int] = None
    mounts_bytes: Optional[int] = None
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self.error is not None or (self.layer_bytes is not None and self.mounts_bytes is not None)

    @property
    def total_bytes(self) -> Optional[int]:
        if self.layer_bytes is None or self.mounts_bytes is None:
            return None
        return self.layer_bytes + self.mounts_bytes
