import wx

from .async_task import AsyncTaskRunner
from .file_system_service import FileSystemService
from .formatting import format_bytes, format_timestamp
from .models import FileProperties

"""File > Properties... / right-click > Properties modal - a single selected
file or folder's name, extension, full path (with a copy button), size,
permissions, and created/modified/accessed dates. A plain wx.Dialog with a
CreateButtonSizer(OK) (no Cancel - this is read-only, there's nothing to
submit), same dialog family as SettingsDialog.

A folder's size is its *recursive* size, which (unlike everything else
shown here) can be arbitrarily slow for a big tree - see
FileSystemService.calculate_folder_size's docstring for why that's a
separate method from get_properties in the first place. This dialog fetches
the fast stat-based properties first (name/extension/path/permissions/dates,
plus a file's own size), then - only for a folder - kicks off the recursive
size calculation through its own AsyncTaskRunner, showing "Calculating..."
in the Size field until it lands. Both fetches go through AsyncTaskRunner
the same as every other FileSystemService call in this app (see
FolderExplorerPage's own docstring) - wx.CallAfter-delivered callbacks are
confirmed (see CLAUDE.md's "Verification performed") to still land correctly
even though this dialog is shown via the blocking ShowModal(), since that
still pumps the same underlying wx event loop.
"""


class PropertiesDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, file_service: FileSystemService, path: str) -> None:
        super().__init__(parent, title="Properties", size=(440, 340))
        self._file_service = file_service
        self._path = path
        self._async = AsyncTaskRunner(self)

        self._build_ui()
        self._load_properties()

        self.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_OK), id=wx.ID_OK)

    def _build_ui(self) -> None:
        outer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=10, hgap=12)
        grid.AddGrowableCol(1)

        self._name_value = self._add_row(grid, "Name:")
        self._extension_value = self._add_row(grid, "Extension:")

        grid.Add(wx.StaticText(self, label="Location:"))
        path_row = wx.BoxSizer(wx.HORIZONTAL)
        self._path_value = wx.StaticText(self, label="")
        path_row.Add(self._path_value, 1, wx.ALIGN_CENTER_VERTICAL)
        copy_path_btn = wx.Button(self, label="⧉", style=wx.BU_EXACTFIT)
        copy_path_btn.SetToolTip("Copy full path to clipboard")
        copy_path_btn.Bind(wx.EVT_BUTTON, self._on_copy_path)
        path_row.Add(copy_path_btn, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 6)
        grid.Add(path_row, 1, wx.EXPAND)

        self._size_value = self._add_row(grid, "Size:")
        self._permissions_value = self._add_row(grid, "Permissions:")
        self._created_value = self._add_row(grid, "Created:")
        self._modified_value = self._add_row(grid, "Modified:")
        self._accessed_value = self._add_row(grid, "Accessed:")

        outer.Add(grid, 1, wx.EXPAND | wx.ALL, 16)
        outer.Add(self.CreateButtonSizer(wx.OK), 0, wx.EXPAND | wx.ALL, 16)
        self.SetSizer(outer)

    def _add_row(self, grid: wx.FlexGridSizer, label: str) -> wx.StaticText:
        grid.Add(wx.StaticText(self, label=label))
        value = wx.StaticText(self, label="")
        grid.Add(value, 1, wx.EXPAND)
        return value

    def _on_copy_path(self, event: wx.CommandEvent) -> None:
        if not wx.TheClipboard.Open():
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(self._path))
        finally:
            wx.TheClipboard.Close()

    # ------------------------------------------------------------------
    # Async loading - same AsyncTaskRunner rule as everywhere else in this
    # app: FileSystemService is only ever called through it, never directly.
    # ------------------------------------------------------------------
    def _load_properties(self) -> None:
        self._async.run(
            work=lambda: self._file_service.get_properties(self._path),
            on_success=self._on_properties_loaded,
            on_error=lambda exc: wx.MessageBox(
                f"Could not read properties: {exc}", "My File Viewer", wx.OK | wx.ICON_ERROR, self
            ),
        )

    def _on_properties_loaded(self, props: FileProperties) -> None:
        self._name_value.SetLabel(props.name)
        self._extension_value.SetLabel(props.extension or "-")
        self._path_value.SetLabel(props.path)
        self._permissions_value.SetLabel(props.permissions)
        self._created_value.SetLabel(format_timestamp(props.created_at))
        self._modified_value.SetLabel(format_timestamp(props.modified_at))
        self._accessed_value.SetLabel(format_timestamp(props.accessed_at))
        if props.is_dir:
            self._size_value.SetLabel("Calculating...")
            self._load_folder_size()
        else:
            self._size_value.SetLabel(format_bytes(props.size_bytes))
        self.Layout()

    def _load_folder_size(self) -> None:
        # A throwaway AsyncTaskRunner, not self._async: by the time this
        # runs, self._async's get_properties call has already finished
        # (this is only ever called from its on_success), so reusing it
        # would work too, but a dedicated runner keeps this consistent with
        # the rest of the app's "one throwaway runner per independent
        # concurrent fetch" convention (see
        # FolderExplorerPage._on_expand_folder) rather than relying on that
        # timing.
        runner = AsyncTaskRunner(self)
        runner.run(
            work=lambda: self._file_service.calculate_folder_size(self._path),
            on_success=self._on_folder_size_loaded,
            on_error=lambda exc: self._size_value.SetLabel("-"),
        )

    def _on_folder_size_loaded(self, size_bytes: int) -> None:
        self._size_value.SetLabel(format_bytes(size_bytes))
        self.Layout()
