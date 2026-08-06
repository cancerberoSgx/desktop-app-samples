from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .models import Image, ImageDependents
from .repositories import ImageRepository


def show_image_details(
    parent: wx.Window,
    reference: str,
    image_repository: ImageRepository,
    initial: Optional[Image] = None,
) -> None:
    """The one entry point every caller should use - constructs, shows, and
    tears down an `ImageDetailsDialog` for one image. `initial`, if the
    caller already has a freshly-loaded `Image` row (ImagesPage does), lets
    the dialog render instantly instead of opening on a blank "Loading..." -
    it's still refreshed from docker right away either way, since that
    snapshot could already be stale by the time the user clicks Info."""
    dialog = ImageDetailsDialog(parent, reference, image_repository, initial=initial)
    dialog.ShowModal()
    dialog.Destroy()


class ImageDetailsDialog(wx.Dialog):
    """Read-only "everything about this one image" popup - repository/tag,
    image ID, size, and exactly which containers/volumes/networks depend on
    it, reusing `ImageRepository.find_dependents` - the same read-only
    lookup ImagesPage's own cascading-remove dialog already relies on to
    show real names rather than just a container count.

    Deliberately its own component rather than folded into ImagesPage -
    same reasoning as `ContainerDetailsDialog`/`VolumeDetailsDialog`: other
    screens will want to show "what does this image affect" from just a
    reference, without loading a full `Image` row themselves - use
    `show_image_details` rather than constructing this directly, so every
    caller goes through the same open/teardown path.

    Everything here is read-only - no remove - this is a detail view, not a
    second copy of ImagesPage's toolbar. There's no Calculate button like
    the Container/Volume detail dialogs have: an image's size is already
    reported directly by `docker image ls`, nothing needs a disposable `du`
    container to measure on demand. Refresh re-fetches both identity and
    dependents - the dependents lookup runs automatically on open and again
    after every Refresh (not gated behind its own button) since it's a
    handful of `docker ps`/`docker inspect` calls, not the dozens of
    throwaway containers class of expensive that keeps that Calculate
    button manual elsewhere."""

    def __init__(
        self,
        parent: wx.Window,
        reference: str,
        image_repository: ImageRepository,
        initial: Optional[Image] = None,
    ) -> None:
        super().__init__(parent, title="Image details", size=(600, 640))
        self._reference = reference
        self._image_repository = image_repository
        self._image: Optional[Image] = initial
        self._gone = False
        # Tracked explicitly rather than read back off AsyncTaskRunner.is_busy()
        # - same reasoning as ContainerDetailsDialog: that flag doesn't clear
        # until after on_success/on_done finish running, i.e. after the
        # exact point _update_button_states needs to read it when called
        # from inside one of those callbacks.
        self._loading = False
        self._loading_dependents = False
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
            ("repository", "Repository"),
            ("tag", "Tag"),
            ("id", "Image ID"),
            ("created", "Created"),
            ("size", "Size"),
            ("status", "Status"),
            ("containers", "Containers"),
        ):
            grid.Add(wx.StaticText(overview_panel, label=f"{label}:"), 0, wx.ALIGN_TOP)
            value = wx.StaticText(overview_panel, label="-")
            grid.Add(value, 0, wx.EXPAND)
            self._overview_fields[key] = value
        overview_box.Add(grid, 0, wx.EXPAND | wx.ALL, 8)
        outer.Add(overview_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        containers_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Used By (containers)")
        self._containers_text = wx.TextCtrl(
            containers_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 70)
        )
        containers_box.Add(self._containers_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(containers_box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        volumes_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Volumes")
        self._volumes_text = wx.TextCtrl(
            volumes_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 50)
        )
        volumes_box.Add(self._volumes_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(volumes_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        networks_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Networks")
        self._networks_text = wx.TextCtrl(
            networks_box.GetStaticBox(), style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 50)
        )
        networks_box.Add(self._networks_text, 1, wx.EXPAND | wx.ALL, 8)
        outer.Add(networks_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

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

        if self._image is not None:
            self._render_overview()
        self._update_button_states()
        self._load_identity()

    # ------------------------------------------------------------------
    # Identity (one `docker image ls` call, same cost as ImagesPage's own Refresh)
    # ------------------------------------------------------------------
    def _load_identity(self) -> None:
        self._loading = True
        self._update_button_states()

        def on_done() -> None:
            self._loading = False
            self._update_button_states()

        self._async.run(
            work=lambda: self._image_repository.get(self._reference),
            on_success=self._on_identity_loaded,
            on_error=self._on_identity_error,
            on_done=on_done,
            # Deliberately no `disable=[self._refresh_btn]` here - see
            # ContainerDetailsDialog's identical comment: AsyncTaskRunner's
            # `disable` bookkeeping unconditionally re-enables its widgets
            # right after on_done runs, which would undo the "stay
            # disabled, the image is gone" state on_done above just set.
        )

    def _on_identity_loaded(self, image: Optional[Image]) -> None:
        if image is None:
            self._gone = True
            self._set_error("This image no longer exists - it may have been removed.")
            return
        self._gone = False
        self._set_error(None)
        self._image = image
        self._render_overview()
        # Deferred rather than called directly: AsyncTaskRunner is
        # single-flight and its is_busy() doesn't clear until on_done runs
        # right after this callback, so calling _load_dependents
        # synchronously here would try to self._async.run() while it still
        # (incorrectly, from this callback's perspective) looks busy, and
        # be ignored.
        wx.CallAfter(self._load_dependents)

    def _on_identity_error(self, exc: Exception) -> None:
        self._set_error(str(exc))

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self._load_identity()

    # ------------------------------------------------------------------
    # Dependents (read-only docker ps/inspect calls - cheap enough to run
    # automatically, unlike Container/VolumeDetailsDialog's `du`-based Calculate)
    # ------------------------------------------------------------------
    def _load_dependents(self) -> None:
        if self._gone:
            return
        self._loading_dependents = True
        self._update_button_states()

        def on_done() -> None:
            self._loading_dependents = False
            self._update_button_states()

        self._async.run(
            work=lambda: self._image_repository.find_dependents(self._reference),
            on_success=self._on_dependents_loaded,
            on_error=self._on_dependents_error,
            on_done=on_done,
        )

    def _on_dependents_loaded(self, dependents: ImageDependents) -> None:
        self._render_dependents(dependents)

    def _on_dependents_error(self, exc: Exception) -> None:
        message = f"Could not determine dependents: {exc}"
        self._containers_text.SetValue(message)
        self._volumes_text.SetValue(message)
        self._networks_text.SetValue(message)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_overview(self) -> None:
        image = self._image
        title = f"{image.repository}:{image.tag}" if not image.is_dangling else f"<dangling> ({image.id})"
        self.SetTitle(f"Image details - {title}")
        self._title_text.SetLabel(title)
        self._overview_fields["repository"].SetLabel(image.repository or "-")
        self._overview_fields["tag"].SetLabel(image.tag or "-")
        self._overview_fields["id"].SetLabel(image.id)
        self._overview_fields["created"].SetLabel(image.created_since or image.created_at or "-")
        self._overview_fields["size"].SetLabel(image.size or "-")
        self._overview_fields["status"].SetLabel(image.status)
        self._overview_fields["containers"].SetLabel(str(image.containers))

        self.Layout()

    def _render_dependents(self, dependents: ImageDependents) -> None:
        if dependents.containers:
            lines = [f"{c.names} ({c.state})" for c in dependents.containers]
            self._containers_text.SetValue("\n".join(lines))
        else:
            self._containers_text.SetValue("Not used by any container")

        self._volumes_text.SetValue(_resource_lines(dependents.volumes))
        self._networks_text.SetValue(_resource_lines(dependents.networks))

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
        # disabled the first time either load finishes.
        busy = self._loading or self._loading_dependents
        self._refresh_btn.Enable(not busy and not self._gone)


def _resource_lines(resources) -> str:
    if not resources:
        return "-"
    lines = []
    for resource in resources:
        line = resource.name
        if resource.shared:
            line += " (shared - also used by another container)"
        lines.append(line)
    return "\n".join(lines)
