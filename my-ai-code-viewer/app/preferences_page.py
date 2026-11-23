from pathlib import Path
from typing import Callable

import wx

from app.db.paths import db_path
from app.providers.claude_code import default_root
from app.providers.opencode import default_db_path
from app.repositories import CLAUDE_PROJECTS_DIR_SETTING_KEY, OPENCODE_DB_PATH_SETTING_KEY, SettingsRepository


def claude_projects_dir(settings: SettingsRepository) -> Path:
    return Path(settings.get(CLAUDE_PROJECTS_DIR_SETTING_KEY) or default_root())


def opencode_db_path(settings: SettingsRepository) -> Path:
    return Path(settings.get(OPENCODE_DB_PATH_SETTING_KEY) or default_db_path())


class PreferencesPage(wx.Panel):
    """Where to read each tool's data from. Saving triggers a rescan."""

    def __init__(self, parent: wx.Window, settings_repository: SettingsRepository,
                 on_saved: Callable[[], None]) -> None:
        super().__init__(parent)
        self._settings = settings_repository
        self._on_saved = on_saved

        title = wx.StaticText(self, label="Preferences")
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 4)
        font.MakeBold()
        title.SetFont(font)

        self._claude_dir = wx.DirPickerCtrl(self, message="Claude Code projects folder",
                                            style=wx.DIRP_USE_TEXTCTRL | wx.DIRP_DIR_MUST_EXIST)
        self._claude_status = wx.StaticText(self)
        self._opencode_db = wx.FilePickerCtrl(self, message="OpenCode database",
                                              wildcard="SQLite database (*.db)|*.db|All files|*",
                                              style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN)
        self._opencode_status = wx.StaticText(self)

        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=12)
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(self, label="Claude Code projects folder"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._claude_dir, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._claude_status)
        grid.Add(wx.StaticText(self, label="OpenCode database"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._opencode_db, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._opencode_status)

        archive = wx.StaticText(self, label=(
            f"Conversation archive: {db_path()}\n"
            "Every conversation found is copied here, so it stays readable even after the\n"
            "tool deletes it (Claude Code prunes transcripts after ~30 days by default)."
        ))
        archive.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        save = wx.Button(self, label="Save && rescan")
        reset = wx.Button(self, label="Reset to defaults")
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(save, 0, wx.RIGHT, 6)
        buttons.Add(reset, 0)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(title, 0, wx.ALL, 16)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)
        sizer.Add(buttons, 0, wx.ALL, 16)
        sizer.Add(archive, 0, wx.LEFT | wx.RIGHT, 16)
        self.SetSizer(sizer)

        save.Bind(wx.EVT_BUTTON, self._on_save)
        reset.Bind(wx.EVT_BUTTON, self._on_reset)
        self._claude_dir.Bind(wx.EVT_DIRPICKER_CHANGED, lambda e: self._update_status())
        self._opencode_db.Bind(wx.EVT_FILEPICKER_CHANGED, lambda e: self._update_status())
        self.load()

    def load(self) -> None:
        self._claude_dir.SetPath(str(claude_projects_dir(self._settings)))
        self._opencode_db.SetPath(str(opencode_db_path(self._settings)))
        self._update_status()

    def _update_status(self) -> None:
        claude_ok = Path(self._claude_dir.GetPath()).is_dir()
        opencode_ok = Path(self._opencode_db.GetPath()).is_file()
        self._claude_status.SetLabel("found" if claude_ok else "not found - Claude Code will be skipped")
        self._opencode_status.SetLabel("found" if opencode_ok else "not found - OpenCode will be skipped")
        self.Layout()

    def _on_save(self, event: wx.CommandEvent) -> None:
        self._settings.set(CLAUDE_PROJECTS_DIR_SETTING_KEY, self._claude_dir.GetPath().strip() or None)
        self._settings.set(OPENCODE_DB_PATH_SETTING_KEY, self._opencode_db.GetPath().strip() or None)
        self._update_status()
        self._on_saved()

    def _on_reset(self, event: wx.CommandEvent) -> None:
        self._claude_dir.SetPath(str(default_root()))
        self._opencode_db.SetPath(str(default_db_path()))
        self._update_status()
