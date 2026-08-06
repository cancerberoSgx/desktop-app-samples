import json
import os
import subprocess
import sqlite3
import threading
from typing import Dict, List, Optional, Set, Tuple

from .models import (
    Container,
    ContainerDiskUsage,
    DependentContainer,
    DependentResource,
    Image,
    ImageDependents,
    Mount,
    Network,
    Volume,
)

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


class MountUnavailableError(RuntimeError):
    """Raised when a mount's disk usage is deliberately not computed - an
    unsupported mount type (tmpfs is memory-backed and has nothing to `du`;
    npipe isn't a filesystem at all), or a volume/bind source that no
    longer exists. Distinct from DockerCommandError - this isn't docker
    failing, it's us declining - so callers can show an expected caveat
    instead of something that reads like an error."""


def _run_docker(args: List[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    """Shells out to `docker`, raising DockerNotAvailableError/
    DockerCommandError as appropriate (see their docstrings). Shared by
    every repository in this module - `ContainerRepository._run` and
    `DiskUsageRepository` both delegate here rather than each shelling out
    independently."""
    try:
        result = subprocess.run(
            [DOCKER_BINARY, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DockerNotAvailableError(
            "The 'docker' command was not found on PATH. Install Docker "
            "and make sure it's available before using this app."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerCommandError(f"docker {' '.join(args)} timed out after {timeout}s.") from exc

    if result.returncode != 0:
        raise DockerCommandError(
            result.stderr.strip() or f"docker {' '.join(args)} failed with exit code {result.returncode}."
        )
    return result.stdout


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
                    created_for=data.get("RunningFor", ""),
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
        return _run_docker(args)


class ImageRepository:
    """Wraps the docker CLI for images - same shell-out-and-parse-
    `{{json .}}` pattern as ContainerRepository, no docker SDK dependency.

    `list()` deliberately omits `-a`: that flag would also surface
    intermediate build-cache layers, which aren't images a user would ever
    remove/prune individually - the no-`-a` list is what `docker images`
    shows by default, and what this screen shows too."""

    def list(self) -> List[Image]:
        output = self._run(["image", "ls", "--format", "{{json .}}"])
        images = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            images.append(
                Image(
                    id=data.get("ID", ""),
                    repository=data.get("Repository", ""),
                    tag=data.get("Tag", ""),
                    created_at=data.get("CreatedAt", ""),
                    created_since=data.get("CreatedSince", ""),
                    size=data.get("Size", ""),
                    containers=_parse_int(data.get("Containers", "")),
                )
            )
        return sorted(images, key=lambda i: (i.repository.lower(), i.tag.lower()))

    def remove(self, reference: str, force: bool = False) -> None:
        args = ["image", "rm"]
        if force:
            args.append("-f")
        args.append(reference)
        self._run(args)

    def prune(self, all_unused: bool = False) -> str:
        """Removes unused images - dangling-only by default, every image
        with zero containers referencing it (docker's own `-a`) when
        `all_unused` is set. Returns docker's own summary text verbatim
        (each deleted image, then "Total reclaimed space: ...") so the
        caller can show the user exactly what happened rather than us
        re-deriving it."""
        args = ["image", "prune", "-f"]
        if all_unused:
            args.append("-a")
        return self._run(args)

    def find_dependents(self, reference: str) -> ImageDependents:
        """Read-only lookup of everything a *cascading* removal of
        `reference` would also take out: every container built from this
        exact image (regardless of state), plus the volumes/networks only
        those containers use - excluding any volume or network some OTHER
        container (outside this set) still needs, so a cascade can't
        quietly break something unrelated. Nothing is removed here.

        `docker ps --filter ancestor=<reference>` is the obvious way to find
        candidate containers, but its own docs describe it as matching
        containers built from this image *or a descendant* of it - a
        container running some other image that was itself built FROM this
        one would also match, which is not what "uses this image" means
        here. So candidates are cross-checked against each container's own
        `.Image` (its exact image ID, via `docker inspect`) rather than
        trusted outright - measured, not assumed."""
        try:
            full_id = self._run(["image", "inspect", "--format", "{{.Id}}", reference]).strip()
        except DockerCommandError:
            full_id = None

        candidates = self._dependent_containers(reference)
        if not candidates:
            return ImageDependents()

        details = self._inspect_containers([c.id for c in candidates])
        if full_id:
            containers = [c for c in candidates if details.get(c.id, {}).get("image_id") == full_id]
        else:
            containers = candidates
        if not containers:
            return ImageDependents()

        container_ids = {c.id for c in containers}
        volume_names: Set[str] = set()
        network_names: Set[str] = set()
        for container in containers:
            info = details.get(container.id, {})
            volume_names.update(info.get("volumes", set()))
            network_names.update(info.get("networks", set()))
        network_names -= _BUILTIN_NETWORKS

        volumes = [
            DependentResource(name=name, shared=self._used_outside("volume", name, container_ids))
            for name in sorted(volume_names)
        ]
        networks = [
            DependentResource(name=name, shared=self._used_outside("network", name, container_ids))
            for name in sorted(network_names)
        ]
        return ImageDependents(containers=containers, volumes=volumes, networks=networks)

    def remove_with_dependents(self, reference: str, dependents: ImageDependents) -> List[str]:
        """Cascading remove: every dependent container (force), then every
        non-shared dependent volume/network, then the image itself -
        continuing past an individual step's failure rather than aborting
        the whole cascade over one bad item, same posture as
        DiskUsageRepository.sum_mounts_bytes. Returns a human-readable note
        per step (what was removed, what was kept and why, what failed) so
        the caller can show the user exactly what happened."""
        notes: List[str] = []
        for container in dependents.containers:
            try:
                self._run(["rm", "-f", container.id])
                notes.append(f'Removed container "{container.names}".')
            except DockerCommandError as exc:
                notes.append(f'Could not remove container "{container.names}": {exc}')

        for volume in dependents.volumes:
            if volume.shared:
                notes.append(f'Kept volume "{volume.name}" - still used by another container.')
                continue
            try:
                self._run(["volume", "rm", volume.name])
                notes.append(f'Removed volume "{volume.name}".')
            except DockerCommandError as exc:
                notes.append(f'Could not remove volume "{volume.name}": {exc}')

        for network in dependents.networks:
            if network.shared:
                notes.append(f'Kept network "{network.name}" - still used by another container.')
                continue
            try:
                self._run(["network", "rm", network.name])
                notes.append(f'Removed network "{network.name}".')
            except DockerCommandError as exc:
                notes.append(f'Could not remove network "{network.name}": {exc}')

        try:
            self._run(["image", "rm", "-f", reference])
            notes.append(f'Removed image "{reference}".')
        except DockerCommandError as exc:
            notes.append(f'Could not remove image "{reference}": {exc}')

        return notes

    # ------------------------------------------------------------------
    def _dependent_containers(self, reference: str) -> List[DependentContainer]:
        output = self._run(["ps", "-a", "--filter", f"ancestor={reference}", "--format", "{{json .}}"])
        containers = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            containers.append(
                DependentContainer(id=data.get("ID", ""), names=data.get("Names", ""), state=data.get("State", ""))
            )
        return containers

    def _inspect_containers(self, container_ids: List[str]) -> Dict[str, dict]:
        """One bulk `docker inspect` for every id's exact image ID, volume
        names, and network names at once - the per-container detail
        `find_dependents` cross-checks candidates against and builds its
        volume/network sets from."""
        if not container_ids:
            return {}
        output = _run_docker(
            [
                "inspect",
                "--format",
                '{{.Id}}{{"\t"}}{{.Image}}{{"\t"}}{{json .Mounts}}{{"\t"}}{{json .NetworkSettings.Networks}}',
                *container_ids,
            ]
        )
        result: Dict[str, dict] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            full_id, image_id, mounts_json, networks_json = line.split("\t", 3)
            volumes = {
                mount.get("Name") or mount.get("Source")
                for mount in json.loads(mounts_json)
                if mount.get("Type") == "volume"
            }
            networks = set(json.loads(networks_json).keys())
            result[full_id[:12]] = {"image_id": image_id, "volumes": volumes, "networks": networks}
        return result

    def _used_outside(self, kind: str, name: str, container_ids: Set[str]) -> bool:
        """True if some container OTHER than the ones about to be removed
        still references this volume/network - `docker ps -a --filter
        volume=.../network=...` covers stopped containers too, not just
        running ones, since a stopped container still "uses" its volumes
        and last-known network in the sense that matters here (removing
        the volume/network out from under it would break it on next
        start)."""
        output = self._run(["ps", "-a", "--filter", f"{kind}={name}", "--format", "{{.ID}}"])
        users = {line.strip() for line in output.splitlines() if line.strip()}
        return bool(users - container_ids)

    @staticmethod
    def _run(args: List[str]) -> str:
        return _run_docker(args)


# Predefined networks docker creates itself and never lets you remove -
# excluded from cascade-removal candidates regardless of "shared" status,
# same reasoning as excluding them from the standalone Networks screen's
# Remove action would be.
_BUILTIN_NETWORKS = {"bridge", "host", "none"}


def _parse_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


class VolumeRepository:
    """Wraps the docker CLI for volumes - same shell-out-and-parse-
    `{{json .}}` pattern as ContainerRepository/ImageRepository.

    `docker volume ls` itself doesn't report which containers use a volume,
    so `list()` cross-references it against every container's own mounts -
    one bulk `docker ps` for identity plus one bulk `docker inspect` for
    mounts, the same two-call shape `DiskUsageRepository.list_targets` uses,
    kept as its own independent (smaller) implementation here rather than
    reused across classes, since this only needs volume names, not the
    bind-mount/tmpfs handling that read-only screen also carries."""

    def list(self) -> List[Volume]:
        volumes = {v.name: v for v in self._ls()}
        if volumes:
            for name, container_name in self._volume_users():
                volume = volumes.get(name)
                if volume is not None:
                    volume.containers += 1
                    volume.container_names.append(container_name)
        return sorted(volumes.values(), key=lambda v: v.name.lower())

    def remove(self, name: str) -> None:
        # No force override exists here for "volume is in use" - `-f` only
        # suppresses a "no such volume" error, so it's not passed at all;
        # ImagesPage-style callers should check `Volume.is_in_use` and
        # explain rather than let this fail with docker's own message.
        self._run(["volume", "rm", name])

    def prune(self, all_unused: bool = False) -> str:
        """Removes unused volumes - anonymous-only by default, every
        volume with zero containers referencing it (docker's own `-a`,
        which despite the name only extends to *named* volumes - anonymous
        ones are always eligible) when `all_unused` is set. Returns
        docker's own summary text verbatim, same reasoning as
        `ImageRepository.prune`."""
        args = ["volume", "prune", "-f"]
        if all_unused:
            args.append("-a")
        return self._run(args)

    # ------------------------------------------------------------------
    def _ls(self) -> List[Volume]:
        output = self._run(["volume", "ls", "--format", "{{json .}}"])
        volumes = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            volumes.append(
                Volume(
                    name=data.get("Name", ""),
                    driver=data.get("Driver", ""),
                    mountpoint=data.get("Mountpoint", ""),
                    scope=data.get("Scope", ""),
                )
            )
        return volumes

    def _volume_users(self):
        """Yields (volume_name, container_name) once per volume mount
        across every container, any state."""
        containers = self._containers()
        if not containers:
            return
        mounts_by_id = self._inspect_volume_mounts([container_id for container_id, _name in containers])
        names_by_id = dict(containers)
        for container_id, volume_names in mounts_by_id.items():
            container_name = names_by_id.get(container_id, container_id)
            for name in volume_names:
                yield name, container_name

    def _containers(self) -> List[Tuple[str, str]]:
        output = self._run(["ps", "-a", "--format", "{{json .}}"])
        result = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            result.append((data.get("ID", ""), data.get("Names", "")))
        return result

    def _inspect_volume_mounts(self, container_ids: List[str]) -> Dict[str, List[str]]:
        if not container_ids:
            return {}
        output = _run_docker(["inspect", "--format", '{{.Id}}{{"\t"}}{{json .Mounts}}', *container_ids])
        result: Dict[str, List[str]] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            full_id, _, mounts_json = line.partition("\t")
            names = [m.get("Name") for m in json.loads(mounts_json) if m.get("Type") == "volume" and m.get("Name")]
            result[full_id[:12]] = names
        return result

    @staticmethod
    def _run(args: List[str]) -> str:
        return _run_docker(args)


class NetworkRepository:
    """Wraps the docker CLI for networks - same shell-out-and-parse-
    `{{json .}}` pattern as the other repositories in this module.

    Unlike volumes, container-to-network usage comes for free: every
    container's own `docker ps` row already reports a comma-separated
    `Networks` field, so `list()` needs no extra `docker inspect` call at
    all to compute `containers`/`container_names`."""

    def list(self) -> List[Network]:
        networks = {n.name: n for n in self._ls()}
        if networks:
            for name, container_name in self._network_users():
                network = networks.get(name)
                if network is not None:
                    network.containers += 1
                    network.container_names.append(container_name)
        return sorted(networks.values(), key=lambda n: n.name.lower())

    def remove(self, name: str) -> None:
        # No force override here either - `-f` only suppresses a "no such
        # network" error, not docker's refusal to remove a network with
        # active endpoints, and bridge/host/none can never be removed at
        # all regardless of flags.
        self._run(["network", "rm", name])

    def prune(self) -> str:
        """Removes every network not used by any container. Unlike
        images/volumes there's no dangling-vs-all distinction for networks
        - `docker network prune` has no `-a` flag - so this takes no
        argument."""
        return self._run(["network", "prune", "-f"])

    # ------------------------------------------------------------------
    def _ls(self) -> List[Network]:
        output = self._run(["network", "ls", "--format", "{{json .}}"])
        networks = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            networks.append(
                Network(
                    id=data.get("ID", ""),
                    name=data.get("Name", ""),
                    driver=data.get("Driver", ""),
                    scope=data.get("Scope", ""),
                )
            )
        return networks

    def _network_users(self):
        """Yields (network_name, container_name) once per network a
        container (any state) is attached to - `docker ps`'s own `Networks`
        field is a comma-separated list, so a container on more than one
        network yields once per network."""
        output = self._run(["ps", "-a", "--format", "{{json .}}"])
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            container_name = data.get("Names", "")
            for name in data.get("Networks", "").split(","):
                name = name.strip()
                if name:
                    yield name, container_name

    @staticmethod
    def _run(args: List[str]) -> str:
        return _run_docker(args)


# Tiny, near-universally-cached image used purely as a vehicle to run `du`
# against a mount from inside the daemon - see DiskUsageRepository's
# docstring for why this beats reading the host path directly.
HELPER_IMAGE = "alpine:latest"
# `du` walking a large volume - or the one-time image pull - can legitimately
# take longer than the plain COMMAND_TIMEOUT_SECONDS budget every other
# docker call uses.
DU_TIMEOUT_SECONDS = 120
PULL_TIMEOUT_SECONDS = 120
# Caps how many `du` helper containers run at once across an entire
# Calculate pass (regardless of how many containers/mounts are involved) -
# keeps a large fleet of volumes from spawning dozens of simultaneous
# `docker run` processes.
MAX_CONCURRENT_DU_RUNS = 4


class DiskUsageRepository:
    """Computes real on-disk usage per container - its own writable layer
    plus every volume/bind mount it uses - for the read-only "Containers
    Disk" screen. Deliberately separate from ContainerRepository: every
    call here is comparatively expensive (a filesystem walk, or a whole
    throwaway container), so it must only ever run when the user presses
    Calculate - never on a timer, never just from opening the page.

    A mount's usage is measured by running a disposable helper container
    that mounts the same volume/path and runs `du` inside it -
    `docker run --rm -v <mount>:/mnt/target:ro alpine du -sk /mnt/target` -
    rather than reading the host path directly. Measured before writing
    this: (1) named volumes are root-owned on Linux and unreadable by an
    ordinary user even when `docker` itself works fine for them, and
    (2) under Docker Desktop (macOS/Windows) volumes live inside its VM and
    are never visible to the host filesystem at all. Routing through the
    daemon sidesteps both and behaves identically on Linux/macOS/Windows.
    `du -sk` (kilobytes) is used rather than a "bytes" flag because
    BusyBox's `-b` means "apparent size", not "output unit: bytes" - `-s`
    and `-k` alone are the common ground between BusyBox and GNU du.
    """

    def __init__(self) -> None:
        self._du_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_DU_RUNS)

    def list_targets(self) -> List[ContainerDiskUsage]:
        """Identity + mount info for every container. Cheap (no `du`, no
        `--size`) - safe to call whenever the page loads or is refreshed,
        independent of the Calculate button."""
        containers = self._identity()
        if not containers:
            return containers

        mounts_by_id = self._inspect_mounts([c.id for c in containers])
        existing_volumes = self._existing_volume_names()

        # A volume or bind path used by more than one container is
        # "shared" - removing just this container won't reclaim it, which
        # matters directly for "what can I delete to free space".
        usage_counts: Dict[Tuple[str, str], int] = {}
        for mounts in mounts_by_id.values():
            for kind, identifier, _destination in mounts:
                if kind in ("volume", "bind"):
                    key = (kind, identifier)
                    usage_counts[key] = usage_counts.get(key, 0) + 1

        for container in containers:
            for kind, identifier, destination in mounts_by_id.get(container.id, []):
                if kind == "volume" and identifier not in existing_volumes:
                    # Referencing a removed volume via `docker run -v
                    # <name>:...` would silently recreate it empty - never
                    # do that from a read-only screen. Note it and move on.
                    container.notes.append(f"volume '{identifier}' no longer exists")
                    continue
                shared = kind in ("volume", "bind") and usage_counts.get((kind, identifier), 0) > 1
                container.mounts.append(Mount(kind=kind, identifier=identifier, destination=destination, shared=shared))
        return containers

    def ensure_helper_image(self) -> None:
        """Pulls HELPER_IMAGE once if it's not already cached, so individual
        `du` calls never pay pull latency (or risk its timeout) themselves.
        Meant to run once per Calculate pass, before any mount is sized."""
        try:
            _run_docker(["image", "inspect", HELPER_IMAGE])
        except DockerCommandError:
            _run_docker(["pull", HELPER_IMAGE], timeout=PULL_TIMEOUT_SECONDS)

    def container_layer_bytes(self, container_ids: List[str]) -> Dict[str, int]:
        """Bulk `docker inspect --size` for every id's writable-layer size
        (`SizeRw`), in one call - this is the moderately expensive
        counterpart to ContainerRepository's `docker ps --size` (both make
        docker compute a filesystem diff), just fetched as raw bytes here
        instead of a pre-formatted string."""
        if not container_ids:
            return {}
        output = _run_docker(
            ["inspect", "--size", "--format", '{{.Id}}{{"\t"}}{{.SizeRw}}', *container_ids],
            timeout=DU_TIMEOUT_SECONDS,
        )
        result = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            full_id, _, size = line.partition("\t")
            try:
                result[full_id[:12]] = int(size)
            except ValueError:
                continue
        return result

    def sum_mounts_bytes(self, mounts: List[Mount]) -> Tuple[int, List[str]]:
        """Sizes every mount and sums them, collecting a note (and
        excluding it from the sum) for any mount that can't be sized
        instead of failing the whole container over one bad mount. Returns
        (total_bytes, notes) rather than mutating anything, so it's safe to
        call from a background thread - the caller applies the result on
        the UI thread."""
        total = 0
        notes: List[str] = []
        for mount in mounts:
            try:
                total += self.mount_usage_bytes(mount)
            except MountUnavailableError as exc:
                notes.append(str(exc))
            except (DockerCommandError, DockerNotAvailableError) as exc:
                notes.append(f"{mount.kind} '{mount.identifier}': {exc}")
        return total, notes

    def mount_usage_bytes(self, mount: Mount) -> int:
        """Sizes one mount via the helper-container `du` described in the
        class docstring. Raises MountUnavailableError for anything
        deliberately not measured (tmpfs has no disk footprint to speak
        of - it's memory; an unrecognized type like Windows' npipe isn't a
        filesystem at all; a volume/bind source that's vanished since
        `list_targets` was called would otherwise get silently recreated -
        an empty directory for a bind mount, or worse, a brand new empty
        *volume* with the old name - by `docker run -v`)."""
        if mount.kind == "tmpfs":
            return 0
        if mount.kind != "volume" and mount.kind != "bind":
            raise MountUnavailableError(f"mount type '{mount.kind}' isn't supported")
        if mount.kind == "bind" and not os.path.exists(mount.identifier):
            raise MountUnavailableError(f"path '{mount.identifier}' no longer exists")
        if mount.kind == "volume":
            # list_targets() already filtered out volumes missing at listing
            # time, but Calculate can run long after that snapshot (or
            # something else on the machine can remove a volume mid-run) -
            # re-check right before the one command that would otherwise
            # silently recreate it empty.
            try:
                _run_docker(["volume", "inspect", mount.identifier])
            except DockerCommandError:
                raise MountUnavailableError(f"volume '{mount.identifier}' no longer exists")

        with self._du_semaphore:
            output = _run_docker(
                ["run", "--rm", "-v", f"{mount.identifier}:/mnt/target:ro", HELPER_IMAGE, "du", "-sk", "/mnt/target"],
                timeout=DU_TIMEOUT_SECONDS,
            )
        kilobytes = output.strip().split()[0]
        return int(kilobytes) * 1024

    def volume_usage_bytes(self, volume_name: str) -> int:
        """Sizes one named volume directly - the same measured,
        safety-checked `du`-via-helper-container path as `mount_usage_bytes`
        (including its just-in-time `docker volume inspect` re-check, so a
        volume removed mid-Calculate doesn't get silently recreated by
        `docker run -v`), for `VolumesPage`'s own Calculate button rather
        than a container's mount. A volume has no `destination` (that's a
        per-container concept - where *that container* mounts it), so the
        synthetic `Mount` below only carries what `mount_usage_bytes`
        actually reads: `kind` and `identifier`."""
        return self.mount_usage_bytes(Mount(kind="volume", identifier=volume_name, destination=""))

    # ------------------------------------------------------------------
    def _identity(self) -> List[ContainerDiskUsage]:
        output = _run_docker(["ps", "-a", "--format", "{{json .}}"])
        containers = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            containers.append(
                ContainerDiskUsage(id=data.get("ID", ""), names=data.get("Names", ""), image=data.get("Image", ""))
            )
        return containers

    def _inspect_mounts(self, container_ids: List[str]) -> Dict[str, List[Tuple[str, str, str]]]:
        if not container_ids:
            return {}
        output = _run_docker(["inspect", "--format", '{{.Id}}{{"\t"}}{{json .Mounts}}', *container_ids])
        result: Dict[str, List[Tuple[str, str, str]]] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            full_id, _, mounts_json = line.partition("\t")
            entries = []
            for raw in json.loads(mounts_json):
                kind = raw.get("Type", "")
                destination = raw.get("Destination", "")
                if kind == "volume":
                    identifier = raw.get("Name", "") or raw.get("Source", "")
                else:
                    identifier = raw.get("Source", "") or destination
                entries.append((kind, identifier, destination))
            result[full_id[:12]] = entries
        return result

    def _existing_volume_names(self) -> set:
        output = _run_docker(["volume", "ls", "-q"])
        return {line.strip() for line in output.splitlines() if line.strip()}


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
