from typing import Optional

import wx

from .async_task import AsyncTaskRunner
from .models import Network
from .repositories import NetworkRepository


def show_network_details(
    parent: wx.Window,
    name: str,
    network_repository: NetworkRepository,
    initial: Optional[Network] = None,
) -> None:
    """The one entry point every caller should use - constructs, shows, and
    tears down a `NetworkDetailsDialog` for one network. `initial`, if the
    caller already has a freshly-loaded `Network` row (NetworksPage does),
    lets the dialog render instantly instead of opening on a blank
    "Loading..." - it's still refreshed from docker right away either way,
    since that snapshot could already be stale by the time the user clicks
    Info."""
    dialog = NetworkDetailsDialog(parent, name, network_repository, initial=initial)
    dialog.ShowModal()
    dialog.Destroy()


class NetworkDetailsDialog(wx.Dialog):
    """Read-only "everything about this one network" popup - driver, scope,
    whether it's one of docker's own built-in networks (bridge/host/none,
    which can never be removed regardless), and the full (untruncated) list
    of containers attached to it.

    Deliberately its own component rather than folded into NetworksPage -
    same reasoning as `ContainerDetailsDialog`/`VolumeDetailsDialog`: other
    screens will want to show "what is this network for" from just a name,
    without loading a full `Network` row themselves - use
    `show_network_details` rather than constructing this directly, so every
    caller goes through the same open/teardown path.

    Everything here is read-only - no remove. There's no Calculate/disk
    usage section like the Container/Volume detail dialogs have either: a
    network has no disk footprint to measure - Refresh (identity + attached
    containers, one `docker network inspect` + one filtered `docker ps`) is
    the only action."""

    def __init__(
        self,
        parent: wx.Window,
        name: str,
        network_repository: NetworkRepository,
        initial: Optional[Network] = None,
    ) -> None:
        super().__init__(parent, title="Network details", size=(560, 460))
        self._name = name
        self._network_repository = network_repository
        self._network: Optional[Network] = initial
        self._gone = False
        # Tracked explicitly rather than read back off AsyncTaskRunner.is_busy()
        # - same reasoning as ContainerDetailsDialog: that flag doesn't clear
        # until after on_success/on_done finish running, i.e. after the
        # exact point _update_button_states needs to read it when called
        # from inside that callback.
        self._loading = False
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
            ("id", "Network ID"),
            ("driver", "Driver"),
            ("scope", "Scope"),
            ("builtin", "Built-in"),
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
            used_by_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120)
        )
        used_by_box.Add(self._used_by_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(used_by_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        button_row.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        button_row.AddStretchSpacer()
        self._close_btn = wx.Button(self, id=wx.ID_CLOSE, label="Close")
        button_row.Add(self._close_btn, 0)
        outer.Add(button_row, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(outer)

        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._close_btn.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))

        if self._network is not None:
            self._render_overview()
        self._update_button_states()
        self._load_identity()

    # ------------------------------------------------------------------
    # Identity (cheap - one `docker network inspect` + one filtered `docker ps`)
    # ------------------------------------------------------------------
    def _load_identity(self) -> None:
        self._loading = True
        self._update_button_states()

        def on_done() -> None:
            self._loading = False
            self._update_button_states()

        self._async.run(
            work=lambda: self._network_repository.get(self._name),
            on_success=self._on_identity_loaded,
            on_error=self._on_identity_error,
            on_done=on_done,
            # Deliberately no `disable=[self._refresh_btn]` here - see
            # ContainerDetailsDialog's identical comment: AsyncTaskRunner's
            # `disable` bookkeeping unconditionally re-enables its widgets
            # right after on_done runs, which would undo the "stay
            # disabled, the network is gone" state on_done above just set.
        )

    def _on_identity_loaded(self, network: Optional[Network]) -> None:
        if network is None:
            self._gone = True
            self._set_error("This network no longer exists - it may have been removed.")
            return
        self._gone = False
        self._set_error(None)
        self._network = network
        self._render_overview()

    def _on_identity_error(self, exc: Exception) -> None:
        self._set_error(str(exc))

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self._load_identity()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_overview(self) -> None:
        network = self._network
        self.SetTitle(f"Network details - {network.name}")
        self._title_text.SetLabel(network.name)
        self._overview_fields["id"].SetLabel(network.id)
        self._overview_fields["driver"].SetLabel(network.driver or "-")
        self._overview_fields["scope"].SetLabel(network.scope or "-")
        self._overview_fields["builtin"].SetLabel("Yes" if network.is_builtin else "No")
        self._overview_fields["status"].SetLabel(network.status)
        self._overview_fields["containers"].SetLabel(str(network.containers))

        self._used_by_text.SetValue(
            "\n".join(network.container_names) if network.container_names else "Not used by any container"
        )

        self.Layout()

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
        # calling this, so reading it here would leave Refresh permanently
        # disabled the first time the load finishes.
        self._refresh_btn.Enable(not self._loading and not self._gone)
