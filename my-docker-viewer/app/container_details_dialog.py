from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .formatting import format_bytes
from .models import Container, ContainerDiskUsage
from .repositories import ContainerRepository, DiskUsageRepository

_KIND_LABELS = {"volume": "volume", "bind": "bind mount", "tmpfs": "tmpfs"}


def _split(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def show_container_details(
    parent: wx.Window,
    container_id: str,
    container_repository: ContainerRepository,
    disk_usage_repository: DiskUsageRepository,
    initial: Optional[Container] = None,
) -> None:
    """The one entry point every caller should use - constructs, shows, and
    tears down a `ContainerDetailsDialog` for one container. `initial`, if
    the caller already has a freshly-loaded `Container` row (ContainersPage
    does), lets the dialog render instantly instead of opening on a blank
    "Loading..." - it's still refreshed from docker right away either way,
    since that snapshot could already be stale by the time the user clicks
    Info."""
    dialog = ContainerDetailsDialog(parent, container_id, container_repository, disk_usage_repository, initial=initial)
    dialog.ShowModal()
    dialog.Destroy()


class ContainerDetailsDialog(wx.Dialog):
    """Read-only "everything about this one container" popup - identity,
    live CPU/mem, ports, networks, and real disk usage (writable layer plus
    every mount, sized on demand exactly like the Containers Disk screen).

    Deliberately its own component rather than folded into ContainersPage:
    other screens reference a container only by id/name without loading a
    full `Container` row themselves (Volumes'/Networks' "Used By" list,
    Images' cascade-remove dependents, ...) and will want this same popup
    later - use `show_container_details` rather than constructing this
    directly, so every caller goes through the same open/teardown path.

    Everything here is read-only - no start/stop/remove - this is a detail
    view, not a second copy of ContainersPage's toolbar. Refresh re-fetches
    identity/live stats (cheap - one `docker ps`/`docker stats` row);
    Calculate sizes the writable layer and every mount (comparably
    expensive to Containers Disk/Volumes' own Calculate - a disposable `du`
    helper container per mount), so it stays opt-in rather than running
    automatically."""

    def __init__(
        self,
        parent: wx.Window,
        container_id: str,
        container_repository: ContainerRepository,
        disk_usage_repository: DiskUsageRepository,
        initial: Optional[Container] = None,
    ) -> None:
        super().__init__(parent, title="Container details", size=(620, 720))
        self._container_id = container_id
        self._container_repository = container_repository
        self._disk_usage_repository = disk_usage_repository
        self._container: Optional[Container] = initial
        self._disk: Optional[ContainerDiskUsage] = None
        self._gone = False
        # Tracked explicitly rather than read back off AsyncTaskRunner.is_busy()
        # - same reasoning as ContainersDiskPage: that flag doesn't clear
        # until after on_success/on_done finish running, i.e. after the
        # exact point _update_button_states needs to read it when called
        # from inside one of those callbacks.
        self._loading_identity = False
        self._loading_disk = False
        self._calculating = False
        # Single-flight is fine here, unlike ContainersDiskPage/VolumesPage's
        # use of run_background for N concurrent per-row jobs - this dialog
        # only ever has one container in flight at a time, so there's no
        # concurrency to gain from that lower-level primitive.
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self._error_text.Hide()

        self._title_text = wx.StaticText(self, label="Loading...")
        title_font = self._title_text.GetFont()
        title_font.SetPointSize(title_font.GetPointSize() + 4)
        title_font.MakeBold()
        self._title_text.SetFont(title_font)
        outer.Add(self._title_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        # Every child of a wx.StaticBoxSizer must be parented to its own
        # wx.StaticBox (GetStaticBox()), not to the dialog itself - wx logs
        # a debug warning ("should be created as child of its wxStaticBox")
        # and can mis-render otherwise.
        overview_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Overview")
        overview_panel = overview_box.GetStaticBox()
        grid = wx.FlexGridSizer(cols=2, vgap=4, hgap=12)
        grid.AddGrowableCol(1, 1)
        self._overview_fields = {}
        for key, label in (
            ("id", "ID"),
            ("image", "Image"),
            ("command", "Command"),
            ("created", "Created"),
            ("status", "Status"),
            ("cpu", "CPU %"),
            ("mem_usage", "Mem usage"),
            ("mem_percent", "Mem %"),
        ):
            grid.Add(wx.StaticText(overview_panel, label=f"{label}:"), 0, wx.ALIGN_TOP)
            value = wx.StaticText(overview_panel, label="-")
            grid.Add(value, 0, wx.EXPAND)
            self._overview_fields[key] = value
        overview_box.Add(grid, 0, wx.EXPAND | wx.ALL, 8)
        outer.Add(overview_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        ports_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Ports")
        self._ports_text = wx.TextCtrl(
            ports_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 60)
        )
        ports_box.Add(self._ports_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(ports_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        networks_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Networks")
        self._networks_text = wx.TextCtrl(
            networks_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 50)
        )
        networks_box.Add(self._networks_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(networks_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        disk_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Disk usage")
        disk_panel = disk_box.GetStaticBox()
        disk_header = wx.BoxSizer(wx.HORIZONTAL)
        self._disk_status_text = wx.StaticText(disk_panel, label="")
        self._disk_status_text.SetForegroundColour(wx.Colour(120, 120, 120))
        disk_header.Add(self._disk_status_text, 1, wx.ALIGN_CENTER_VERTICAL)
        self._calculate_btn = wx.Button(disk_panel, label="Calculate")
        disk_header.Add(self._calculate_btn, 0)
        disk_box.Add(disk_header, 0, wx.EXPAND | wx.ALL, 8)
        self._disk_text = wx.TextCtrl(
            disk_panel, value="Loading mounts...", style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120)
        )
        disk_box.Add(self._disk_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        outer.Add(disk_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        button_row.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        button_row.AddStretchSpacer()
        self._close_btn = wx.Button(self, id=wx.ID_CLOSE, label="Close")
        button_row.Add(self._close_btn, 0)
        outer.Add(button_row, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(outer)

        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._calculate_btn.Bind(wx.EVT_BUTTON, self._on_calculate)
        self._close_btn.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))

        if self._container is not None:
            self._render_overview()
        self._update_button_states()
        self._load_identity()

    # ------------------------------------------------------------------
    # Identity + live stats (cheap - docker ps/stats scoped to one id)
    # ------------------------------------------------------------------
    def _load_identity(self) -> None:
        self._loading_identity = True
        self._update_button_states()

        def on_done() -> None:
            self._loading_identity = False
            self._update_button_states()

        self._async.run(
            work=lambda: self._container_repository.get(self._container_id),
            on_success=self._on_identity_loaded,
            on_error=self._on_identity_error,
            on_done=on_done,
            disable=[self._refresh_btn],
        )

    def _on_identity_loaded(self, container: Optional[Container]) -> None:
        if container is None:
            self._gone = True
            self._set_error("This container no longer exists - it may have been removed.")
            return
        self._gone = False
        self._set_error(None)
        self._container = container
        self._render_overview()
        # Deferred rather than called directly: AsyncTaskRunner is
        # single-flight and its is_busy() doesn't clear until on_done runs
        # (right after this callback returns), so a second self._async.run()
        # call made right here would be silently ignored.
        wx.CallAfter(self._load_disk_identity)

    def _on_identity_error(self, exc: Exception) -> None:
        self._set_error(str(exc))

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self._load_identity()

    # ------------------------------------------------------------------
    # Disk mount identity (cheap - no `du`) + Calculate (expensive)
    # ------------------------------------------------------------------
    def _load_disk_identity(self) -> None:
        if self._gone:
            return
        self._loading_disk = True
        self._update_button_states()

        def on_done() -> None:
            self._loading_disk = False
            self._update_button_states()

        self._async.run(
            work=lambda: self._disk_usage_repository.get_target(self._container_id),
            on_success=self._on_disk_identity_loaded,
            on_error=self._on_disk_error,
            on_done=on_done,
        )

    def _on_disk_identity_loaded(self, disk: Optional[ContainerDiskUsage]) -> None:
        if disk is None:
            self._disk_status_text.SetLabel("Container no longer exists.")
            return
        self._disk = disk
        self._render_disk()

    def _on_disk_error(self, exc: Exception) -> None:
        self._disk_text.SetValue(f"Could not load disk usage: {exc}")

    def _on_calculate(self, event: Optional[wx.CommandEvent]) -> None:
        if self._calculating or self._disk is None:
            return
        self._calculating = True
        self._disk.layer_bytes = None
        self._disk.mounts_bytes = None
        self._disk.notes = []
        self._disk.error = None
        self._disk_status_text.SetLabel("Calculating...")
        self._render_disk()
        self._update_button_states()

        container_id = self._container_id
        mounts = list(self._disk.mounts)

        def work():
            self._disk_usage_repository.ensure_helper_image()
            layer_bytes_by_id = self._disk_usage_repository.container_layer_bytes([container_id])
            mounts_bytes, notes = self._disk_usage_repository.sum_mounts_bytes(mounts)
            return layer_bytes_by_id.get(container_id, 0), mounts_bytes, notes

        def success(result) -> None:
            layer_bytes, mounts_bytes, notes = result
            self._disk.layer_bytes = layer_bytes
            self._disk.mounts_bytes = mounts_bytes
            self._disk.notes.extend(notes)
            self._calculating = False
            self._disk_status_text.SetLabel("")
            self._render_disk()
            self._update_button_states()

        def error(exc: Exception) -> None:
            self._disk.error = str(exc)
            self._calculating = False
            self._disk_status_text.SetLabel("")
            self._render_disk()
            self._update_button_states()

        self._async.run(work=work, on_success=success, on_error=error, disable=[self._calculate_btn, self._refresh_btn])

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_overview(self) -> None:
        container = self._container
        self.SetTitle(f"Container details - {container.names}")
        self._title_text.SetLabel(container.names)
        self._overview_fields["id"].SetLabel(container.id)
        self._overview_fields["image"].SetLabel(container.image)
        self._overview_fields["command"].SetLabel(container.command or "-")
        self._overview_fields["created"].SetLabel(container.created_for or container.created_at or "-")
        self._overview_fields["status"].SetLabel(f"{container.status} ({container.state})")
        self._overview_fields["cpu"].SetLabel(container.cpu_percent or "-")
        self._overview_fields["mem_usage"].SetLabel(container.mem_usage or "-")
        self._overview_fields["mem_percent"].SetLabel(container.mem_percent or "-")

        ports = _split(container.ports)
        self._ports_text.SetValue("\n".join(ports) if ports else "No published ports")

        networks = _split(container.networks)
        self._networks_text.SetValue("\n".join(networks) if networks else "-")

        self.Layout()

    def _render_disk(self) -> None:
        disk = self._disk
        if disk is None:
            self._disk_text.SetValue("Loading mounts...")
            return

        lines: List[str] = []
        if disk.error is not None:
            lines.append(f"Error: {disk.error}")
        else:
            layer_text = format_bytes(disk.layer_bytes) if disk.layer_bytes is not None else "Not calculated"
            lines.append(f"Writable layer: {layer_text}")
            if disk.mounts:
                for mount in disk.mounts:
                    label = _KIND_LABELS.get(mount.kind, mount.kind)
                    line = f"  - {label}: {mount.identifier} -> {mount.destination}"
                    if mount.shared:
                        line += " (shared - also used by another container)"
                    lines.append(line)
            else:
                lines.append("  (no volumes or bind mounts)")
            if disk.mounts_bytes is not None:
                lines.append(f"Mounts total: {format_bytes(disk.mounts_bytes)}")
            if disk.total_bytes is not None:
                lines.append(f"Total: {format_bytes(disk.total_bytes)}")
            for note in disk.notes:
                lines.append(f"Note: {note}")

        self._disk_text.SetValue("\n".join(lines))

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    def _update_button_states(self) -> None:
        # Deliberately NOT self._async.is_busy() - see the comment on
        # self._loading_identity's declaration: that flag is still True
        # for the whole duration of the on_success callback that's often
        # the one calling this, so reading it here would leave both
        # buttons permanently disabled the first time either load finishes.
        busy = self._loading_identity or self._loading_disk or self._calculating
        self._refresh_btn.Enable(not busy and not self._gone)
        self._calculate_btn.Enable(not busy and not self._gone and self._disk is not None)
