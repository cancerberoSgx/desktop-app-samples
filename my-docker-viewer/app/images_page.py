from typing import List, Optional

import wx

from .async_task import AsyncTaskRunner
from .formatting import size_sort_key
from .models import Image
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


class ImagesPage(wx.Panel):
    """List every local docker image - repository:tag, size, and how many
    containers (running or stopped) reference it - filter by name/status,
    remove one, or prune every unused image at once.

    Unlike ContainersPage there is no auto-refresh timer: an image list
    only changes when something (this app, another docker client, a build)
    actually adds or removes an image, not every few seconds like CPU/mem -
    a manual Refresh is enough, so this page never hits the docker CLI on
    its own."""

    def __init__(self, parent: wx.Window, repository: ImageRepository) -> None:
        super().__init__(parent)
        self._repository = repository
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

        # Same pattern as ContainersPage._on_remove: docker itself will
        # refuse to remove an image referenced by any container (running or
        # stopped) without -f, and -f still won't remove one behind a
        # *running* container - that case surfaces as a docker error via
        # on_error rather than something this dialog tries to pre-empt.
        force = image.containers > 0
        label = image.reference
        prompt = (
            f'Image "{label}" is used by {image.containers} container(s). Force remove it?'
            if force
            else f'Remove image "{label}"?'
        )
        confirm = wx.MessageBox(prompt, "Confirm remove", wx.YES_NO | wx.ICON_WARNING, self)
        if confirm != wx.YES:
            return

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
