import os
from pathlib import Path
from typing import Callable, Dict

import wx

from app import theme
from app.conversation_view import CONVERSATION_OPTIONS
from app.db.paths import db_path
from app.providers.claude_code import default_root
from app.providers.codex import CodexProvider, default_home as default_codex_home
from app.providers.opencode import default_db_path
from app.repositories import (CLAUDE_PROJECTS_DIR_SETTING_KEY, CODEX_HOME_SETTING_KEY, OPENCODE_DB_PATH_SETTING_KEY,
                              SettingsRepository)


def claude_projects_dir(settings: SettingsRepository) -> Path:
    return Path(settings.get(CLAUDE_PROJECTS_DIR_SETTING_KEY) or default_root())


def opencode_db_path(settings: SettingsRepository) -> Path:
    return Path(settings.get(OPENCODE_DB_PATH_SETTING_KEY) or default_db_path())


def codex_home(settings: SettingsRepository) -> Path:
    return Path(settings.get(CODEX_HOME_SETTING_KEY) or default_codex_home())


class PreferencesDialog(wx.Dialog):
    """File > Preferences. "Sources" tab: where to read each tool's data
    from. "Conversation" tab: the viewer options also on the conversation
    toolbar (same settings keys). `on_saved(sources_changed)` lets the frame
    rescan only when a source path changed."""

    def __init__(self, parent: wx.Window, settings_repository: SettingsRepository,
                 on_saved: Callable[[bool], None]) -> None:
        super().__init__(parent, title="Preferences", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._settings = settings_repository
        self._on_saved = on_saved

        notebook = wx.Notebook(self)
        notebook.AddPage(self._build_sources_page(notebook), "Sources")
        notebook.AddPage(self._build_conversation_page(notebook), "Conversation")
        notebook.AddPage(self._build_visual_page(notebook), "Visual")

        reset = wx.Button(self, label="Reset to defaults")
        save = wx.Button(self, wx.ID_OK, label="Save")
        save.SetDefault()
        cancel = wx.Button(self, wx.ID_CANCEL)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(reset, 0)
        buttons.AddStretchSpacer()
        buttons.Add(cancel, 0, wx.RIGHT, 6)
        buttons.Add(save, 0)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(sizer)
        # Fit the height, but not the width: pickers grow with long paths.
        self.Fit()
        self.SetSize((700, self.GetSize().height))
        self.SetMinSize((520, self.GetSize().height))
        self.CentreOnParent()

        save.Bind(wx.EVT_BUTTON, self._on_save)
        reset.Bind(wx.EVT_BUTTON, self._on_reset)
        self._claude_dir.Bind(wx.EVT_DIRPICKER_CHANGED, lambda e: self._update_status())
        self._opencode_db.Bind(wx.EVT_FILEPICKER_CHANGED, lambda e: self._update_status())
        self._codex_dir.Bind(wx.EVT_DIRPICKER_CHANGED, lambda e: self._update_status())
        self.load()

    def _build_sources_page(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        self._sources_page = page
        self._claude_dir = wx.DirPickerCtrl(page, message="Claude Code projects folder",
                                            style=wx.DIRP_USE_TEXTCTRL | wx.DIRP_DIR_MUST_EXIST)
        self._claude_status = wx.StaticText(page)
        self._opencode_db = wx.FilePickerCtrl(page, message="OpenCode database",
                                              wildcard="SQLite database (*.db)|*.db|All files|*",
                                              style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN)
        self._opencode_status = wx.StaticText(page)
        self._codex_dir = wx.DirPickerCtrl(page, message="Codex home folder",
                                           style=wx.DIRP_USE_TEXTCTRL | wx.DIRP_DIR_MUST_EXIST)
        self._codex_status = wx.StaticText(page)

        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=12)
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(page, label="Claude Code projects folder"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._claude_dir, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._claude_status)
        grid.Add(wx.StaticText(page, label="OpenCode database"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._opencode_db, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._opencode_status)
        grid.Add(wx.StaticText(page, label="Codex home folder"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._codex_dir, 1, wx.EXPAND)
        grid.AddSpacer(0)
        grid.Add(self._codex_status)

        grey = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
        archive_path = wx.StaticText(page, label=f"Conversation archive: {db_path()}",
                                     style=wx.ST_ELLIPSIZE_MIDDLE)
        archive_path.SetToolTip(str(db_path()))
        archive_path.SetForegroundColour(grey)
        archive = wx.StaticText(page, label=(
            "Every conversation found is copied here, so it stays readable even after the "
            "tool deletes it (Claude Code prunes transcripts after ~30 days by default). "
            "Changing a source rescans on save."
        ))
        archive.Wrap(600)
        archive.SetForegroundColour(grey)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        sizer.Add(archive_path, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        sizer.AddSpacer(4)
        sizer.Add(archive, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        page.SetSizer(sizer)
        return page

    def _build_conversation_page(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        self._option_boxes: Dict[str, wx.CheckBox] = {}
        sizer = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(page, label="How conversations are shown. The same options are on "
                                          "the conversation toolbar.")
        intro.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(intro, 0, wx.ALL, 12)
        for key, label, _default, tooltip in CONVERSATION_OPTIONS:
            box = wx.CheckBox(page, label=label.replace("&", "&&"))
            self._option_boxes[key] = box
            hint = wx.StaticText(page, label=tooltip)
            hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            hint.Wrap(540)
            sizer.Add(box, 0, wx.LEFT | wx.RIGHT, 12)
            sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        page.SetSizer(sizer)
        return page

    def _build_visual_page(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        grey = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
        system = "dark" if theme.system_is_dark() else "light"
        labels = [f"{label} (currently {system})" if key == theme.SYSTEM else label
                  for key, label in theme.THEMES]
        self._theme = wx.RadioBox(page, label="Theme", choices=labels, majorDimension=1,
                                  style=wx.RA_SPECIFY_COLS)
        hint = wx.StaticText(page, label=(
            "System follows the desktop's light/dark setting. The conversation view switches "
            "immediately; menus, lists and dialogs switch after a restart."
        ))
        hint.Wrap(600)
        hint.SetForegroundColour(grey)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._theme, 0, wx.ALL, 12)
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        if "GTK_THEME" in os.environ:
            note = wx.StaticText(page, label=(
                f"GTK_THEME={os.environ['GTK_THEME']} is set in the environment, so menus, lists "
                "and dialogs keep that theme."
            ))
            note.Wrap(600)
            note.SetForegroundColour(grey)
            sizer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        page.SetSizer(sizer)
        return page

    def _theme_key(self) -> str:
        return theme.THEMES[self._theme.GetSelection()][0]

    def _select_theme(self, value: str) -> None:
        keys = [key for key, _label in theme.THEMES]
        self._theme.SetSelection(keys.index(theme.normalize(value)))

    def load(self) -> None:
        self._claude_dir.SetPath(str(claude_projects_dir(self._settings)))
        self._opencode_db.SetPath(str(opencode_db_path(self._settings)))
        self._codex_dir.SetPath(str(codex_home(self._settings)))
        for key, _label, default, _tooltip in CONVERSATION_OPTIONS:
            self._option_boxes[key].SetValue(self._settings.get_bool(key, default=default))
        self._select_theme(self._settings.get(theme.THEME_SETTING_KEY))
        self._update_status()

    def _update_status(self) -> None:
        claude_ok = Path(self._claude_dir.GetPath()).is_dir()
        opencode_ok = Path(self._opencode_db.GetPath()).is_file()
        self._claude_status.SetLabel("found" if claude_ok else "not found - Claude Code will be skipped")
        self._opencode_status.SetLabel("found" if opencode_ok else "not found - OpenCode will be skipped")
        codex_ok = CodexProvider(Path(self._codex_dir.GetPath())).is_available()
        self._codex_status.SetLabel("found" if codex_ok else "no sessions folder - Codex will be skipped")
        self._sources_page.Layout()

    def _on_save(self, event: wx.CommandEvent) -> None:
        before = self._source_paths()
        self._settings.set(CLAUDE_PROJECTS_DIR_SETTING_KEY, self._claude_dir.GetPath().strip() or None)
        self._settings.set(OPENCODE_DB_PATH_SETTING_KEY, self._opencode_db.GetPath().strip() or None)
        self._settings.set(CODEX_HOME_SETTING_KEY, self._codex_dir.GetPath().strip() or None)
        for key, box in self._option_boxes.items():
            self._settings.set_bool(key, box.GetValue())
        self._settings.set(theme.THEME_SETTING_KEY, self._theme_key())
        after = self._source_paths()
        self.EndModal(wx.ID_OK)
        self._on_saved(before != after)

    def _source_paths(self):
        return (claude_projects_dir(self._settings), opencode_db_path(self._settings), codex_home(self._settings))

    def _on_reset(self, event: wx.CommandEvent) -> None:
        self._claude_dir.SetPath(str(default_root()))
        self._opencode_db.SetPath(str(default_db_path()))
        self._codex_dir.SetPath(str(default_codex_home()))
        for key, _label, default, _tooltip in CONVERSATION_OPTIONS:
            self._option_boxes[key].SetValue(default)
        self._select_theme(theme.DEFAULT_THEME)
        self._update_status()
