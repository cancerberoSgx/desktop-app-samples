from typing import Callable, List, Optional, Tuple

import wx

from .async_task import AsyncTaskRunner
from .formatting import size_sort_key
from .image_details_dialog import show_image_details
from .models import Image, ImageDependents
from .repositories import DockerCommandError, DockerNotAvailableError, ImageRepository

STATUS_CHOICES = ["All", "In use", "Unused", "Dangling"]

# (header label, initial width) per list column, and the matching sort-key
# function - both index-aligned to the columns as inserted into the wx.ListCtrl.
_COLUMNS = [
    ("Repository", 220),
    ("Tag", 110),
    ("Image ID", 110),
    ("Created", 150),
    ("Size", 100),
    ("Containers", 90),
    ("Status", 90),
]
_SORT_KEYS = [
    lambda i: i.repository.lower(),
    lambda i: i.tag.lower(),
    lambda i: i.id,
    # created_at is docker's raw timestamp, not the friendlier created_since
    # shown in the column - lexicographic order on it matches chronological
    # order, same trick ContainersPage plays with created_at/created_for.
    lambda i: i.created_at,
    lambda i: size_sort_key(i.size),
    lambda i: i.containers,
    lambda i: i.status,
]


class _RemoveImageDialog(wx.Dialog):
    """Shown instead of a plain yes/no confirm whenever the image being
    removed has at least one dependent container - lets the user pick
    between removing just the image (same as before: still needs -f, and
    still fails outright behind a *running* container regardless) or
    cascading the removal to every dependent container plus the
    volumes/networks only those containers use, to reclaim the most space
    in one step. `cascade` holds the user's choice after ShowModal()
    returns wx.ID_OK; `dependents` is read-only input, never mutated here."""

    def __init__(self, parent: wx.Window, image: Image, dependents: ImageDependents) -> None:
        super().__init__(parent, title="Remove image")
        self.cascade = False

        outer = wx.BoxSizer(wx.VERTICAL)
        summary = wx.StaticText(
            self,
            label=(
                f'"{image.reference}" ({image.size or "unknown size"}) is used by '
                f"{len(dependents.containers)} container(s)."
            ),
        )
        outer.Add(summary, 0, wx.ALL, 12)

        self._image_only_radio = wx.RadioButton(self, label="Remove image only", style=wx.RB_GROUP)
        self._cascade_radio = wx.RadioButton(
            self, label="Remove image and all associated containers/volumes/networks"
        )
        self._image_only_radio.SetValue(True)
        outer.Add(self._image_only_radio, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        outer.Add(self._cascade_radio, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        # Only shown once "cascade" is picked - keeps the common "just
        # remove the image" path from being cluttered by a wall of detail
        # most of the time.
        self._detail_text = wx.StaticText(self, label=self._describe(dependents))
        self._detail_text.SetForegroundColour(wx.Colour(120, 120, 120))
        self._detail_text.Wrap(420)
        self._detail_text.Show(False)
        outer.Add(self._detail_text, 0, wx.EXPAND | wx.ALL, 12)

        button_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK, label="Remove")
        ok_btn.SetDefault()
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        button_sizer.AddButton(ok_btn)
        button_sizer.AddButton(cancel_btn)
        button_sizer.Realize()
        outer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizerAndFit(outer)
        self.CentreOnParent()

        self._image_only_radio.Bind(wx.EVT_RADIOBUTTON, self._on_radio)
        self._cascade_radio.Bind(wx.EVT_RADIOBUTTON, self._on_radio)
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)

    @staticmethod
    def _describe(dependents: ImageDependents) -> str:
        lines = []
        if dependents.containers:
            lines.append("Containers: " + ", ".join(c.names for c in dependents.containers))
        removed_volumes = [v.name for v in dependents.volumes if not v.shared]
        kept_volumes = [v.name for v in dependents.volumes if v.shared]
        if removed_volumes:
            lines.append("Volumes: " + ", ".join(removed_volumes))
        if kept_volumes:
            lines.append("Volumes kept (also used elsewhere): " + ", ".join(kept_volumes))
        removed_networks = [n.name for n in dependents.networks if not n.shared]
        kept_networks = [n.name for n in dependents.networks if n.shared]
        if removed_networks:
            lines.append("Networks: " + ", ".join(removed_networks))
        if kept_networks:
            lines.append("Networks kept (also used elsewhere): " + ", ".join(kept_networks))
        return "\n".join(lines) if lines else "Nothing else associated with this image."

    def _on_radio(self, event: wx.CommandEvent) -> None:
        self.cascade = self._cascade_radio.GetValue()
        self._detail_text.Show(self.cascade)
        self.Layout()
        self.Fit()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        self.cascade = self._cascade_radio.GetValue()
        self.EndModal(wx.ID_OK)


class ImagesPage(wx.Panel):
    """List every local docker image - repository:tag, size, and how many
    containers (running or stopped) reference it - filter by name/status,
    remove one, or prune every unused image at once.

    Unlike ContainersPage there is no auto-refresh timer: an image list
    only changes when something (this app, another docker client, a build)
    actually adds or removes an image, not every few seconds like CPU/mem -
    a manual Refresh is enough, so this page never hits the docker CLI on
    its own.

    Info (or double-clicking/pressing Enter on a row) opens
    `ImageDetailsDialog` (`app/image_details_dialog.py`) for the selected
    image - full identity plus exactly which containers/volumes/networks
    depend on it, via the same `find_dependents` lookup the cascading-
    remove dialog above already uses. That dialog is its own reusable
    component precisely so other screens can open the same view later from
    just a reference, without needing a loaded `Image` row of their own.

    Like VolumesPage, the list is multi-select (`wx.LC_REPORT` without
    `wx.LC_SINGLE_SEL`) - ctrl-click/shift-click and shift+Up/Down are
    wx.ListCtrl's own native selection behavior, nothing custom here.
    Removing exactly one image still goes through the full single-image
    flow above (cascade dialog when it has dependent containers); removing
    a batch of more than one uses a simpler all-or-nothing-per-item path
    (`_on_remove_multiple`) that force-removes any in-use image directly
    rather than presenting N cascade dialogs in a row - see that method's
    docstring. Info only makes sense for one image at a time, so it stays
    disabled unless the selection is exactly one row."""

    def __init__(
        self,
        parent: wx.Window,
        repository: ImageRepository,
        on_containers_changed: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        # A cascading remove can delete containers this page never loaded
        # itself (it only tracks images) - this optional hook lets
        # MainFrame wire it to ContainersPage.reload so that page doesn't
        # sit stale showing containers that no longer exist until the user
        # happens to revisit it.
        self._on_containers_changed = on_containers_changed
        self._images: List[Image] = []
        self._visible: List[Image] = []
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self._error_text.Hide()

        # Row 1: actions.
        actions_bar = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        self._info_btn = wx.Button(self, label="Info")
        self._remove_btn = wx.Button(self, label="Remove")
        self._prune_btn = wx.Button(self, label="Prune unused")
        actions_bar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._info_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._remove_btn, 0, wx.RIGHT, 8)
        actions_bar.Add(self._prune_btn, 0)

        actions_bar.AddStretchSpacer()

        self._loading_text = wx.StaticText(self, label="")
        self._loading_text.SetForegroundColour(wx.Colour(120, 120, 120))
        actions_bar.Add(self._loading_text, 0, wx.ALIGN_CENTER_VERTICAL)

        outer.Add(actions_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        # Row 2: filters.
        filters_bar = wx.BoxSizer(wx.HORIZONTAL)
        filters_bar.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_filter = wx.SearchCtrl(self, size=(160, -1))
        self._name_filter.ShowCancelButton(True)
        filters_bar.Add(self._name_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        filters_bar.Add(wx.StaticText(self, label="Status:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._status_choice = wx.Choice(self, choices=STATUS_CHOICES)
        self._status_choice.SetSelection(0)
        filters_bar.Add(self._status_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        self._include_tagged_checkbox = wx.CheckBox(self, label="Prune: include tagged unused images")
        filters_bar.Add(self._include_tagged_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

        outer.Add(filters_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)

        # No wx.LC_SINGLE_SEL - this list is deliberately multi-select (see
        # the class docstring), same as VolumesPage.
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self._column_labels = [label for label, _width in _COLUMNS]
        for index, (label, width) in enumerate(_COLUMNS):
            self._list.InsertColumn(index, label, width=width)
        outer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        # Sortable columns: repository.list() already returns images sorted
        # by repository/tag, so that's also the initial header sort state.
        self._sort_column = 0
        self._sort_ascending = True

        self._name_filter.Bind(wx.EVT_TEXT, self._on_filter_changed)
        self._name_filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self._on_name_filter_cancel)
        self._status_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._info_btn.Bind(wx.EVT_BUTTON, self._on_info)
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._prune_btn.Bind(wx.EVT_BUTTON, self._on_prune)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        # Double-click (or Enter on a focused row) an image to jump straight
        # to its details, same shortcut ContainersPage gives its own rows -
        # Info is still there on the toolbar for a single click.
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_info)
        # Delete key as a shortcut for Remove, covering the whole selection
        # same as clicking the button would - mirrors VolumesPage.
        self._list.Bind(wx.EVT_LIST_KEY_DOWN, self._on_list_key_down)

        self._update_button_states(None)
        self._update_column_headers()
        self.reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def reload(self) -> None:
        if self._async.is_busy():
            return
        self._set_loading(True)
        self._async.run(
            work=self._repository.list,
            on_success=self._on_loaded,
            on_error=self._on_load_error,
            on_done=lambda: self._set_loading(False),
            disable=[self._refresh_btn],
        )

    def _set_loading(self, loading: bool) -> None:
        self._loading_text.SetLabel("Loading..." if loading else "")
        # The table only goes visibly blank on the very first load (or if
        # the last result was empty) - once rows are showing, a refresh
        # keeps them in place and this label is the only feedback.
        if loading and not self._images:
            self._show_loading_placeholder()

    def _show_loading_placeholder(self) -> None:
        self._list.DeleteAllItems()
        self._list.InsertItem(0, "Loading images...")

    def _on_loaded(self, images: List[Image]) -> None:
        self._set_error(None)
        self._images = images
        self._populate_list()

    def _on_load_error(self, exc: Exception) -> None:
        self._set_error(str(exc))
        self._images = []
        self._populate_list()

    def _set_error(self, message: Optional[str]) -> None:
        if message:
            self._error_text.SetLabel(message)
            self._error_text.Show()
        else:
            self._error_text.Hide()
        self.Layout()

    # ------------------------------------------------------------------
    # Filtering / rendering
    # ------------------------------------------------------------------
    def _filtered_images(self) -> List[Image]:
        name = self._name_filter.GetValue().strip().lower()
        status = self._status_choice.GetStringSelection()

        result = []
        for image in self._images:
            if name and name not in image.repository.lower() and name not in image.tag.lower():
                continue
            if status != "All" and image.status != status:
                continue
            result.append(image)
        return result

    def _populate_list(self) -> None:
        # Preserve the whole selection, not just one row - a resort/filter/
        # refresh mid multi-select shouldn't collapse it down to one item.
        selected_refs = {i.reference for i in self._selected_images()}

        self._visible = self._filtered_images()
        self._sort_visible()
        self._list.DeleteAllItems()
        for row, image in enumerate(self._visible):
            self._list.InsertItem(row, image.repository)
            self._list.SetItem(row, 1, image.tag)
            self._list.SetItem(row, 2, image.id)
            self._list.SetItem(row, 3, image.created_since or image.created_at)
            self._list.SetItem(row, 4, image.size or "-")
            self._list.SetItem(row, 5, str(image.containers))
            self._list.SetItem(row, 6, image.status)
            if image.reference in selected_refs:
                self._list.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

        self._update_button_states(None)

    def _sort_visible(self) -> None:
        key_func = _SORT_KEYS[self._sort_column]
        self._visible.sort(key=key_func, reverse=not self._sort_ascending)

    def _on_col_click(self, event: wx.ListEvent) -> None:
        column = event.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._update_column_headers()
        self._populate_list()

    def _update_column_headers(self) -> None:
        for index, label in enumerate(self._column_labels):
            if index == self._sort_column:
                label += " ↑" if self._sort_ascending else " ↓"
            column_info = self._list.GetColumn(index)
            column_info.SetText(label)
            self._list.SetColumn(index, column_info)

    def _selected_image(self) -> Optional[Image]:
        """The single selected image - for actions (Info, the cascade-
        aware single-image remove flow) that only make sense against
        exactly one row. Returns `None` for zero *or* more than one
        selected, unlike `_selected_images()` below."""
        images = self._selected_images()
        return images[0] if len(images) == 1 else None

    def _selected_images(self) -> List[Image]:
        """Every currently selected image, in list order - this is a
        multi-select list (see the class docstring), so callers that act on
        "the selection" (Remove) should use this, not `_selected_image()`."""
        images = []
        index = self._list.GetFirstSelected()
        while index != -1:
            if index < len(self._visible):
                images.append(self._visible[index])
            index = self._list.GetNextSelected(index)
        return images

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        images = self._selected_images()
        # Info only makes sense for exactly one image at a time.
        self._info_btn.Enable(len(images) == 1)
        self._remove_btn.Enable(bool(images))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self._populate_list()

    def _on_name_filter_cancel(self, event: wx.CommandEvent) -> None:
        self._name_filter.SetValue("")
        self._populate_list()

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self.reload()

    def _on_info(self, event: wx.Event) -> None:
        # Bound to both the Info button (wx.CommandEvent) and double-click/
        # Enter on a row (wx.EVT_LIST_ITEM_ACTIVATED, a wx.ListEvent) - the
        # activation event names the exact row that was double-clicked/
        # entered, which - unlike _selected_image() - still resolves to one
        # image even if a multi-select happens to include others.
        if isinstance(event, wx.ListEvent):
            index = event.GetIndex()
            image = self._visible[index] if 0 <= index < len(self._visible) else None
        else:
            image = self._selected_image()
        if image is None:
            return
        show_image_details(self, image.reference, self._repository, initial=image)

    def _on_list_key_down(self, event: wx.ListEvent) -> None:
        if event.GetKeyCode() == wx.WXK_DELETE:
            self._on_remove(event)
        else:
            event.Skip()

    def _apply_removed(self, references: List[str]) -> None:
        """Mirrors VolumesPage._apply_removed - drop the given images from
        the already-loaded list and re-render immediately instead of
        waiting on a full `docker image ls` round trip. Takes a list (not
        one reference) so a multi-select removal renders as a single batch
        rather than one re-render per image."""
        removed = set(references)
        self._images = [i for i in self._images if i.reference not in removed]
        self._populate_list()

    def _on_remove(self, event: wx.Event) -> None:
        images = self._selected_images()
        if not images:
            return
        if len(images) > 1:
            self._on_remove_multiple(images)
            return
        image = images[0]

        if image.containers == 0:
            # Nothing to cascade to - keep the plain yes/no confirm rather
            # than firing off a find_dependents() round trip that would
            # come back empty anyway.
            confirm = wx.MessageBox(
                f'Remove image "{image.reference}"?', "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self
            )
            if confirm == wx.YES:
                self._run_remove(image, force=False)
            return

        # At least one container references this image - look up exactly
        # what a cascade would take out before asking, so the dialog can
        # show real names/counts instead of just "N container(s)".
        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not check what uses "{image.reference}":\n\n{exc}',
                "Remove failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.find_dependents(image.reference),
            on_success=lambda dependents: self._prompt_remove(image, dependents),
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )

    def _prompt_remove(self, image: Image, dependents: ImageDependents) -> None:
        if dependents.is_empty:
            # docker's own container count on this image disagreed with
            # what find_dependents() could actually pin down (e.g. the
            # ancestor filter matched nothing after the exact-image-ID
            # cross-check) - nothing concrete to cascade to, so fall back
            # to the plain path rather than showing an empty dialog.
            confirm = wx.MessageBox(
                f'Image "{image.reference}" is used by {image.containers} container(s). Force remove it?',
                "Confirm remove",
                wx.YES_NO | wx.ICON_WARNING,
                self,
            )
            if confirm == wx.YES:
                self._run_remove(image, force=True)
            return

        dialog = _RemoveImageDialog(self, image, dependents)
        result = dialog.ShowModal()
        cascade = dialog.cascade
        dialog.Destroy()
        if result != wx.ID_OK:
            return

        if cascade:
            self._run_cascade_remove(image, dependents)
        else:
            self._run_remove(image, force=True)

    def _run_remove(self, image: Image, force: bool) -> None:
        label = image.reference

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not remove "{label}":\n\n{exc}',
                "Remove failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.remove(image.reference, force=force),
            on_success=lambda _result: self._apply_removed([image.reference]),
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )

    def _run_cascade_remove(self, image: Image, dependents: ImageDependents) -> None:
        def on_success(notes: List[str]) -> None:
            wx.MessageBox("\n".join(notes), "Remove complete", wx.OK | wx.ICON_INFORMATION, self)
            if self._on_containers_changed:
                self._on_containers_changed()
            # See _on_prune's comment on why this has to go through
            # wx.CallAfter rather than calling reload() directly here.
            wx.CallAfter(self.reload)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(
                f'Could not remove "{image.reference}" and its associated resources:\n\n{exc}',
                "Remove failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._async.run(
            work=lambda: self._repository.remove_with_dependents(image.reference, dependents),
            on_success=on_success,
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )

    def _on_remove_multiple(self, images: List[Image]) -> None:
        """Removing more than one image at once skips the cascade dialog
        entirely, unlike the single-image path above - presenting that
        dialog once per in-use image would just be N modal prompts in a
        row for a batch. Instead any image still referenced by a container
        is force-removed directly (the same fallback `_prompt_remove`
        already uses when find_dependents() can't pin down anything
        concrete to cascade to), and the confirm prompt says so up front so
        the user isn't surprised that a batch remove doesn't offer to take
        dependent containers/volumes/networks with it. Continues past an
        individual failure rather than aborting the whole batch, same
        posture as VolumesPage's batch remove."""
        in_use = [i for i in images if i.containers > 0]
        prompt = f"Remove {len(images)} images?"
        if in_use:
            prompt += (
                f"\n\n{len(in_use)} still referenced by a container - those "
                "will be force-removed. Removing a single in-use image "
                "offers to cascade to its containers/volumes/networks too; "
                "that option isn't available for a multi-image batch."
            )
        confirm = wx.MessageBox(prompt, "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        jobs = [(image.reference, image.containers > 0) for image in images]

        def work() -> List[Tuple[str, Optional[str]]]:
            results = []
            for reference, force in jobs:
                try:
                    self._repository.remove(reference, force=force)
                    results.append((reference, None))
                except (DockerCommandError, DockerNotAvailableError) as exc:
                    results.append((reference, str(exc)))
            return results

        def on_success(results: List[Tuple[str, Optional[str]]]) -> None:
            succeeded = [reference for reference, error in results if error is None]
            failed = [(reference, error) for reference, error in results if error is not None]
            if succeeded:
                self._apply_removed(succeeded)
            if failed:
                details = "\n".join(f'"{reference}": {error}' for reference, error in failed)
                wx.MessageBox(
                    f"Could not remove {len(failed)} of {len(jobs)} image(s):\n\n{details}",
                    "Remove failed",
                    wx.OK | wx.ICON_ERROR,
                    self,
                )

        def on_error(exc: Exception) -> None:
            # Only reachable for a truly unexpected failure - work() itself
            # catches both known docker exception types per-item above.
            wx.MessageBox(f"Could not remove images:\n\n{exc}", "Remove failed", wx.OK | wx.ICON_ERROR, self)

        self._async.run(
            work=work,
            on_success=on_success,
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )

    def _on_prune(self, event: wx.CommandEvent) -> None:
        all_unused = self._include_tagged_checkbox.GetValue()
        prompt = (
            "Remove every unused image - including tagged images not used by "
            "any container, not just dangling ones?"
            if all_unused
            else "Remove every dangling (untagged) image?"
        )
        confirm = wx.MessageBox(prompt, "Confirm prune", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

        def on_success(summary: str) -> None:
            wx.MessageBox(summary.strip() or "Nothing to remove.", "Prune complete", wx.OK | wx.ICON_INFORMATION, self)
            # A prune can delete an arbitrary number of images identified
            # only by docker's own unused/dangling rules - reconciling that
            # against self._images in place isn't worth it, so this is the
            # one action on this page that does a full reload rather than
            # an optimistic patch. wx.CallAfter defers it to the next event
            # loop tick because AsyncTaskRunner is single-flight and hasn't
            # cleared its busy flag yet at this point in the callback -
            # calling reload() synchronously here would just be ignored.
            wx.CallAfter(self.reload)

        def on_error(exc: Exception) -> None:
            wx.MessageBox(f"Could not prune images:\n\n{exc}", "Prune failed", wx.OK | wx.ICON_ERROR, self)

        self._async.run(
            work=lambda: self._repository.prune(all_unused=all_unused),
            on_success=on_success,
            on_error=on_error,
            disable=[self._remove_btn, self._prune_btn],
        )
