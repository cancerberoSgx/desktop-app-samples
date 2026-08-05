from typing import Callable, List, Optional

import wx

from .async_task import AsyncTaskRunner
from .formatting import size_sort_key
from .models import Image, ImageDependents
from .repositories import ImageRepository

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
    its own."""

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
        outer.Add(wx.StaticText(self, label="Images"), 0, wx.ALL, 12)

        self._error_text = wx.StaticText(self, label="")
        self._error_text.SetForegroundColour(wx.Colour(180, 30, 30))
        outer.Add(self._error_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self._error_text.Hide()

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        toolbar.Add(wx.StaticText(self, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._name_filter = wx.SearchCtrl(self, size=(160, -1))
        self._name_filter.ShowCancelButton(True)
        toolbar.Add(self._name_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        toolbar.Add(wx.StaticText(self, label="Status:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self._status_choice = wx.Choice(self, choices=STATUS_CHOICES)
        self._status_choice.SetSelection(0)
        toolbar.Add(self._status_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        self._include_tagged_checkbox = wx.CheckBox(self, label="Prune: include tagged unused images")
        toolbar.Add(self._include_tagged_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

        toolbar.AddStretchSpacer()

        self._loading_text = wx.StaticText(self, label="")
        self._loading_text.SetForegroundColour(wx.Colour(120, 120, 120))
        toolbar.Add(self._loading_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._refresh_btn = wx.Button(self, label="Refresh")
        self._remove_btn = wx.Button(self, label="Remove")
        self._prune_btn = wx.Button(self, label="Prune unused")
        toolbar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._remove_btn, 0, wx.RIGHT, 8)
        toolbar.Add(self._prune_btn, 0)

        outer.Add(toolbar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN)
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
        self._remove_btn.Bind(wx.EVT_BUTTON, self._on_remove)
        self._prune_btn.Bind(wx.EVT_BUTTON, self._on_prune)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._update_button_states)
        self._list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)

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
        selected = self._selected_image()
        selected_ref = selected.reference if selected else None

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
            if selected_ref and image.reference == selected_ref:
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
        index = self._list.GetFirstSelected()
        if index == -1 or index >= len(self._visible):
            return None
        return self._visible[index]

    def _update_button_states(self, event: Optional[wx.ListEvent]) -> None:
        image = self._selected_image()
        self._remove_btn.Enable(image is not None)

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

    def _apply_removed(self, reference: str) -> None:
        """Mirrors ContainersPage._apply_removed - drop the image from the
        already-loaded list and re-render immediately instead of waiting on
        a full `docker image ls` round trip."""
        self._images = [i for i in self._images if i.reference != reference]
        self._populate_list()

    def _on_remove(self, event: wx.CommandEvent) -> None:
        image = self._selected_image()
        if image is None:
            return

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
            on_success=lambda _result: self._apply_removed(image.reference),
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
