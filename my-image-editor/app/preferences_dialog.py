import os
from typing import Callable

import wx

from app import theme
from app.db.paths import db_path
from app.repositories import (DEFAULT_JPEG_QUALITY, DEFAULT_OPEN_ZOOM, DEFAULT_PNG_COMPRESS_LEVEL,
                              JPEG_QUALITY_SETTING_KEY, OPEN_ZOOM_ACTUAL, OPEN_ZOOM_FIT, OPEN_ZOOM_SETTING_KEY,
                              PNG_COMPRESS_LEVEL_SETTING_KEY, SettingsRepository)

OPEN_ZOOM_CHOICES = ((OPEN_ZOOM_FIT, "Fit to window (never enlarged)"), (OPEN_ZOOM_ACTUAL, "Actual size (100%)"))


def jpeg_quality(settings: SettingsRepository) -> int:
    return settings.get_int_in(JPEG_QUALITY_SETTING_KEY, DEFAULT_JPEG_QUALITY, 1, 100)


def png_compress_level(settings: SettingsRepository) -> int:
    return settings.get_int_in(PNG_COMPRESS_LEVEL_SETTING_KEY, DEFAULT_PNG_COMPRESS_LEVEL, 0, 9)


def open_zoom(settings: SettingsRepository) -> str:
    value = settings.get(OPEN_ZOOM_SETTING_KEY)
    return value if value in (OPEN_ZOOM_FIT, OPEN_ZOOM_ACTUAL) else DEFAULT_OPEN_ZOOM


class PreferencesDialog(wx.Dialog):
    """File > Preferences. "Files": how images are saved and opened.
    "Visual": the theme. `on_saved()` lets the frame re-apply the theme."""

    def __init__(self, parent: wx.Window, settings_repository: SettingsRepository,
                 on_saved: Callable[[], None]) -> None:
        super().__init__(parent, title="Preferences", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._settings = settings_repository
        self._on_saved = on_saved

        notebook = wx.Notebook(self)
        notebook.AddPage(self._build_files_page(notebook), "Files")
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
        self.Fit()
        self.SetMinSize(self.GetSize())
        self.CentreOnParent()

        save.Bind(wx.EVT_BUTTON, self._on_save)
        reset.Bind(wx.EVT_BUTTON, self._on_reset)
        self.load()

    def _build_files_page(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        grey = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
        self._jpeg = wx.Slider(page, minValue=1, maxValue=100, style=wx.SL_HORIZONTAL | wx.SL_VALUE_LABEL,
                               size=(260, -1))
        self._png = wx.SpinCtrl(page, min=0, max=9)
        self._open_zoom = wx.RadioBox(page, label="When an image is opened",
                                      choices=[label for _key, label in OPEN_ZOOM_CHOICES],
                                      majorDimension=1, style=wx.RA_SPECIFY_COLS)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=12)
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(page, label="JPEG quality"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._jpeg, 1, wx.EXPAND)
        grid.Add(wx.StaticText(page, label="PNG compression (0-9)"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self._png, 0)

        note = wx.StaticText(page, label=(
            "Higher JPEG quality = bigger files (90 is a good default). PNG is lossless either way; "
            "more compression is only slower. Saving writes the shapes into the file - they stay "
            "editable in the app until it is closed."))
        note.Wrap(440)
        note.SetForegroundColour(grey)
        db = wx.StaticText(page, label=f"Settings: {db_path()}", style=wx.ST_ELLIPSIZE_MIDDLE)
        db.SetToolTip(str(db_path()))
        db.SetForegroundColour(grey)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        sizer.Add(self._open_zoom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        sizer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        sizer.Add(db, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
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
            "System follows the desktop's light/dark setting. The canvas switches immediately; "
            "menus, buttons and dialogs switch after a restart."
        ))
        hint.Wrap(440)
        hint.SetForegroundColour(grey)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._theme, 0, wx.ALL, 12)
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        if "GTK_THEME" in os.environ:
            note = wx.StaticText(page, label=(
                f"GTK_THEME={os.environ['GTK_THEME']} is set in the environment, so menus, buttons "
                "and dialogs keep that theme."
            ))
            note.Wrap(440)
            note.SetForegroundColour(grey)
            sizer.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        page.SetSizer(sizer)
        return page

    def _theme_key(self) -> str:
        return theme.THEMES[self._theme.GetSelection()][0]

    def _select_theme(self, value) -> None:
        keys = [key for key, _label in theme.THEMES]
        self._theme.SetSelection(keys.index(theme.normalize(value)))

    def _select_open_zoom(self, value: str) -> None:
        keys = [key for key, _label in OPEN_ZOOM_CHOICES]
        self._open_zoom.SetSelection(keys.index(value))

    def load(self) -> None:
        self._jpeg.SetValue(jpeg_quality(self._settings))
        self._png.SetValue(png_compress_level(self._settings))
        self._select_open_zoom(open_zoom(self._settings))
        self._select_theme(self._settings.get(theme.THEME_SETTING_KEY))

    def _on_save(self, event: wx.CommandEvent) -> None:
        self._settings.set_int(JPEG_QUALITY_SETTING_KEY, self._jpeg.GetValue())
        self._settings.set_int(PNG_COMPRESS_LEVEL_SETTING_KEY, self._png.GetValue())
        self._settings.set(OPEN_ZOOM_SETTING_KEY, OPEN_ZOOM_CHOICES[self._open_zoom.GetSelection()][0])
        self._settings.set(theme.THEME_SETTING_KEY, self._theme_key())
        self.EndModal(wx.ID_OK)
        self._on_saved()

    def _on_reset(self, event: wx.CommandEvent) -> None:
        self._jpeg.SetValue(DEFAULT_JPEG_QUALITY)
        self._png.SetValue(DEFAULT_PNG_COMPRESS_LEVEL)
        self._select_open_zoom(DEFAULT_OPEN_ZOOM)
        self._select_theme(theme.DEFAULT_THEME)
