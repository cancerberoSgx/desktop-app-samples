from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .formatting import volume_size_text
from .models import Volume
from .repositories import DiskUsageRepository, VolumeRepository


def show_volume_details(
    parent: wx.Window,
    name: str,
    volume_repository: VolumeRepository,
    disk_usage_repository: DiskUsageRepository,
    initial: Optional[Volume] = None,
) -> None:
    """The one entry point every caller should use - constructs, shows, and
    tears down a `VolumeDetailsDialog` for one volume. `initial`, if the
    caller already has a freshly-loaded `Volume` row (VolumesPage does),
    lets the dialog render instantly instead of opening on a blank
    "Loading..." - it's still refreshed from docker right away either way,
    since that snapshot could already be stale by the time the user clicks
    Info."""
    dialog = VolumeDetailsDialog(parent, name, volume_repository, disk_usage_repository, initial=initial)
    dialog.ShowModal()
    dialog.Destroy()


class VolumeDetailsDialog(wx.Dialog):
    """Read-only "everything about this one volume" popup - driver,
    mountpoint, scope, the full (untruncated) list of containers/images
    that use it, and its real disk usage, sized on demand via the same
    helper-container `du` approach as VolumesPage's own Size column
    (`DiskUsageRepository.volume_usage_bytes`).

    Deliberately its own component rather than folded into VolumesPage -
    same reasoning as `ContainerDetailsDialog`: other screens will want to
    show "what is this volume for" from just a name, without loading a full
    `Volume` row themselves - use `show_volume_details` rather than
    constructing this directly, so every caller goes through the same
    open/teardown path.

    Everything here is read-only - no remove - this is a detail view, not a
    second copy of VolumesPage's toolbar. Refresh re-fetches identity (cheap
    - one `docker volume inspect` + one filtered `docker ps`). Unlike
    VolumesPage's own Calculate (deliberately manual, since a machine can
    have dozens of volumes with nothing to do with what's on screen), this
    dialog only ever sizes the ONE volume it's showing, so - like
    ContainerDetailsDialog - Calculate runs automatically as soon as the
    volume's identity is known (on open, and again after every Refresh);
    the button just lets it be re-run on demand too."""

    def __init__(
        self,
        parent: wx.Window,
        name: str,
        volume_repository: VolumeRepository,
        disk_usage_repository: DiskUsageRepository,
        initial: Optional[Volume] = None,
    ) -> None:
        super().__init__(parent, title="Volume details", size=(560, 480))
        self._name = name
        self._volume_repository = volume_repository
        self._disk_usage_repository = disk_usage_repository
        self._volume: Optional[Volume] = initial
        self._gone = False
        # Tracked explicitly rather than read back off AsyncTaskRunner.is_busy()
        # - same reasoning as ContainerDetailsDialog: that flag doesn't clear
        # until after on_success/on_done finish running, i.e. after the
        # exact point _update_button_states needs to read it when called
        # from inside one of those callbacks.
        self._loading = False
        self._calculating = False
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
            ("driver", "Driver"),
            ("mountpoint", "Mountpoint"),
            ("scope", "Scope"),
            ("status", "Status"),
            ("containers", "Containers"),
        ):
            grid.Add(wx.StaticText(overview_panel, label=f"{label}:"), 0, wx.ALIGN_TOP)
            value = wx.StaticText(overview_panel, label="-")
            grid.Add(value, 0, wx.EXPAND)
            self._overview_fields[key] = value
        overview_box.Add(grid, 0, wx.EXPAND | wx.ALL, 8)
        outer.Add(overview_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        used_by_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Used By")
        self._used_by_text = wx.TextCtrl(
            used_by_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 70)
        )
        used_by_box.Add(self._used_by_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(used_by_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        images_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Images")
        self._images_text = wx.TextCtrl(
            images_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 70)
        )
        images_box.Add(self._images_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(images_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        disk_box = wx.StaticBoxSizer(wx.HORIZONTAL, self, "Disk usage")
        disk_panel = disk_box.GetStaticBox()
        self._disk_status_text = wx.StaticText(disk_panel, label="")
        self._disk_status_text.SetForegroundColour(wx.Colour(120, 120, 120))
        disk_box.Add(self._disk_status_text, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 8)
        self._calculate_btn = wx.Button(disk_panel, label="Calculate")
        disk_box.Add(self._calculate_btn, 0, wx.ALL, 8)
        outer.Add(disk_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

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

        if self._volume is not None:
            self._render_overview()
        self._update_disk_summary()
        self._update_button_states()
        self._load_identity()

    # ------------------------------------------------------------------
    # Identity (cheap - one `docker volume inspect` + one filtered `docker ps`)
    # ------------------------------------------------------------------
    def _load_identity(self) -> None:
        self._loading = True
        self._update_button_states()

        def on_done() -> None:
            self._loading = False
            self._update_button_states()

        self._async.run(
            work=lambda: self._volume_repository.get(self._name),
            on_success=self._on_identity_loaded,
            on_error=self._on_identity_error,
            on_done=on_done,
            # Deliberately no `disable=[self._refresh_btn]` here: our own
            # on_done above already re-derives the button's correct state
            # via _update_button_states() (including "stay disabled, the
            # volume is gone"). AsyncTaskRunner's `disable` bookkeeping
            # unconditionally re-enables its widgets right after on_done
            # runs, which would silently undo that - see the "volume no
            # longer exists" case: _gone becomes True, on_done disables
            # Refresh correctly, and then `disable=` would flip it back on.
        )

    def _on_identity_loaded(self, volume: Optional[Volume]) -> None:
        if volume is None:
            self._gone = True
            self._set_error("This volume no longer exists - it may have been removed.")
            return
        self._gone = False
        self._set_error(None)
        # A fresh Volume from get() starts with no size, same as list()'s
        # rows after a VolumesPage Refresh - re-run Calculate below rather
        # than carry over a size that could now be wrong (mount changed,
        # volume recreated, ...).
        self._volume = volume
        self._render_overview()
        self._update_disk_summary()
        # Deferred rather than called directly: AsyncTaskRunner is
        # single-flight and its is_busy() doesn't clear until on_done runs
        # right after this callback, so calling _on_calculate synchronously
        # here would try to self._async.run() while it still (incorrectly,
        # from this callback's perspective) looks busy, and be ignored.
        wx.CallAfter(self._on_calculate, None)

    def _on_identity_error(self, exc: Exception) -> None:
        self._set_error(str(exc))

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self._load_identity()

    # ------------------------------------------------------------------
    # Calculate - reuses DiskUsageRepository.volume_usage_bytes, the exact
    # same helper-container `du` VolumesPage's own Size column runs.
    # ------------------------------------------------------------------
    def _on_calculate(self, event: Optional[wx.CommandEvent]) -> None:
        if self._calculating or self._volume is None or self._gone:
            return
        self._calculating = True
        self._volume.size_bytes = None
        self._volume.size_error = None
        self._update_disk_summary()
        self._update_button_states()

        name = self._name

        def work():
            self._disk_usage_repository.ensure_helper_image()
            return self._disk_usage_repository.volume_usage_bytes(name)

        def success(size_bytes: int) -> None:
            self._volume.size_bytes = size_bytes
            self._calculating = False
            self._update_disk_summary()
            self._update_button_states()

        def error(exc: Exception) -> None:
            self._volume.size_error = str(exc)
            self._calculating = False
            self._update_disk_summary()
            self._update_button_states()

        self._async.run(work=work, on_success=success, on_error=error, disable=[self._calculate_btn, self._refresh_btn])

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_overview(self) -> None:
        volume = self._volume
        self.SetTitle(f"Volume details - {volume.name}")
        self._title_text.SetLabel(volume.name)
        self._overview_fields["driver"].SetLabel(volume.driver or "-")
        self._overview_fields["mountpoint"].SetLabel(volume.mountpoint or "-")
        self._overview_fields["scope"].SetLabel(volume.scope or "-")
        self._overview_fields["status"].SetLabel(volume.status)
        self._overview_fields["containers"].SetLabel(str(volume.containers))

        self._used_by_text.SetValue(
            "\n".join(volume.container_names) if volume.container_names else "Not used by any container"
        )
        self._images_text.SetValue("\n".join(volume.image_names) if volume.image_names else "-")

        self.Layout()

    def _update_disk_summary(self) -> None:
        if self._loading:
            label = "Loading..."
        elif self._volume is None:
            label = "Not calculated"
        else:
            # volume_size_text checks `volume.name in pending` for the
            # "Calculating..." state - {self._name} while our own Calculate
            # is running reuses that exact VolumesPage Size-column renderer
            # unchanged rather than re-deriving the same state machine here.
            pending = {self._name} if self._calculating else set()
            label = volume_size_text(self._volume, pending)
        self._disk_status_text.SetLabel(label)

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    def _update_button_states(self) -> None:
        # Deliberately NOT self._async.is_busy() - see the comment on
        # self._loading's declaration: that flag is still True for the
        # whole duration of the on_success callback that's often the one
        # calling this, so reading it here would leave both buttons
        # permanently disabled the first time the load finishes.
        busy = self._loading or self._calculating
        self._refresh_btn.Enable(not busy and not self._gone)
        self._calculate_btn.Enable(not busy and not self._gone and self._volume is not None)
