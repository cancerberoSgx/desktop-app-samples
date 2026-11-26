import json
import os
import sys
from pathlib import Path
from typing import List, Optional

import wx

from app import theme
from app.about_dialog import AboutDialog
from app.canvas import Canvas
from app.db.connection import get_connection
from app.db.migrator import run_migrations
from app.db.paths import migrations_dir
from app.dialogs import ResizeDialog, ask_text
from app.editor import imageio
from app.editor.document import Document
from app.editor.editor import Editor
from app.editor.shapes import Style
from app.editor.tools import TOOL_CLASSES, Mods
from app.preferences_dialog import PreferencesDialog, jpeg_quality, open_zoom, png_compress_level
from app.repositories import (DEFAULT_STYLE_SETTING_KEY, LAST_DIR_SETTING_KEY, OPEN_ZOOM_FIT,
                              SIDEBAR_COLLAPSED_SETTING_KEY, SettingsRepository)
from app.sidebar import Sidebar

APP_NAME = "My Image Editor"
THEME_CHECK_INTERVAL_MS = 60_000
DEFAULT_SIZE = (1200, 800)
MIN_SIZE = (500, 350)

# Status bar fields
STATUS_HINT, STATUS_POINTER, STATUS_SIZE, STATUS_ZOOM = range(4)

_KEY_NAMES = {
    wx.WXK_RETURN: "enter", wx.WXK_NUMPAD_ENTER: "enter", wx.WXK_ESCAPE: "escape",
    wx.WXK_DELETE: "delete", wx.WXK_BACK: "delete", wx.WXK_NUMPAD_DELETE: "delete",
    wx.WXK_LEFT: "left", wx.WXK_RIGHT: "right", wx.WXK_UP: "up", wx.WXK_DOWN: "down",
    wx.WXK_NUMPAD_LEFT: "left", wx.WXK_NUMPAD_RIGHT: "right", wx.WXK_NUMPAD_UP: "up", wx.WXK_NUMPAD_DOWN: "down",
}
_TEXT_ENTRY_TYPES = (wx.TextCtrl, wx.SpinCtrl, wx.SpinCtrlDouble, wx.ComboBox, wx.Choice)


def read_setting_before_app(key: str) -> Optional[str]:
    """One setting, read before wx.App exists (the theme must be known
    before the toolkit starts). Never fails start-up."""
    try:
        conn = get_connection()
        try:
            run_migrations(conn, migrations_dir())
            return SettingsRepository(conn).get(key)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a broken DB is reported later by the frame
        return None


class MainFrame(wx.Frame):
    def __init__(self, initial_path: Optional[str] = None) -> None:
        super().__init__(None, title=APP_NAME, size=DEFAULT_SIZE)

        conn = get_connection()
        run_migrations(conn, migrations_dir())
        self._conn = conn
        self.settings_repository = SettingsRepository(conn)
        self._restore_geometry()

        style_json = self.settings_repository.get(DEFAULT_STYLE_SETTING_KEY)
        try:
            style = Style.from_dict(json.loads(style_json) if style_json else None)
        except ValueError:
            style = Style()
        self.editor = Editor(style)
        self.editor.request_text = lambda initial: ask_text(self, initial, self.editor.style)

        self.CreateStatusBar(4)
        self.SetStatusWidths([-1, 110, 120, 70])

        body = wx.Panel(self)
        self.canvas = Canvas(body, self.editor, on_pointer=self._show_pointer, on_view_changed=self._update_status,
                             on_files_dropped=self._on_files_dropped, on_empty_click=self.open_dialog)
        self.sidebar = Sidebar(body, self.editor,
                               collapsed=self.settings_repository.get_bool(SIDEBAR_COLLAPSED_SETTING_KEY),
                               on_collapsed_changed=self._on_sidebar_collapsed, on_done=self.canvas.SetFocus)
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.sidebar, 0, wx.EXPAND)
        sizer.Add(wx.StaticLine(body, style=wx.LI_VERTICAL), 0, wx.EXPAND)
        sizer.Add(self.canvas, 1, wx.EXPAND)
        body.SetSizer(sizer)

        self._build_menu_bar()
        self.editor.subscribe(self._on_editor)

        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        # "System" theme: follow the desktop switching light/dark. GTK doesn't
        # always send this event, so a timer re-checks too.
        self.Bind(wx.EVT_SYS_COLOUR_CHANGED, self._on_sys_colour_changed)
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.refresh_theme(), self._timer)
        self._timer.Start(THEME_CHECK_INTERVAL_MS)
        self.refresh_theme()

        self._update_title()
        self._update_status()
        self.canvas.SetFocus()
        if initial_path:
            wx.CallAfter(self.open_path, Path(initial_path))

    # ------------------------------------------------------------------
    # Window geometry (remembered across runs)
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        s = self.settings_repository
        width, height = s.get_int("window_width"), s.get_int("window_height")
        x, y = s.get_int("window_x"), s.get_int("window_y")
        if width and height:
            self.SetSize(max(width, MIN_SIZE[0]), max(height, MIN_SIZE[1]))
        # Only reuse the position if it still lands on a connected display.
        if x is not None and y is not None and wx.Display.GetFromPoint(wx.Point(x + 40, y + 40)) != wx.NOT_FOUND:
            self.SetPosition(wx.Point(x, y))
        else:
            self.Centre()
        # The un-maximized rect, tracked so un-maximizing next run restores it.
        self._normal_rect = self.GetRect()
        if s.get_bool("window_maximized"):
            self.Maximize()
        self.Bind(wx.EVT_SIZE, self._on_geometry_changed)
        self.Bind(wx.EVT_MOVE, self._on_geometry_changed)

    def _on_geometry_changed(self, event: wx.Event) -> None:
        event.Skip()
        if not self.IsMaximized() and not self.IsIconized() and not self.IsFullScreen():
            self._normal_rect = self.GetRect()

    def _save_geometry(self) -> None:
        s = self.settings_repository
        rect = self._normal_rect
        s.set_int("window_x", rect.x)
        s.set_int("window_y", rect.y)
        s.set_int("window_width", rect.width)
        s.set_int("window_height", rect.height)
        s.set_bool("window_maximized", self.IsMaximized())

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def refresh_theme(self) -> None:
        dark = theme.resolve_dark(self.settings_repository.get(theme.THEME_SETTING_KEY), theme.system_is_dark())
        self.canvas.set_dark(dark)

    def _on_sys_colour_changed(self, event: wx.SysColourChangedEvent) -> None:
        event.Skip()
        wx.CallAfter(self.refresh_theme)
        wx.CallAfter(self.sidebar.refresh_icons)

    def _on_prefs_saved(self) -> None:
        self.refresh_theme()
        self.SetStatusText("Preferences saved", STATUS_HINT)
        if theme.needs_restart(self.settings_repository.get(theme.THEME_SETTING_KEY)):
            answer = wx.MessageBox(
                "The canvas uses the new theme already. Menus, buttons and dialogs "
                "switch after a restart.\n\nRestart now?",
                "Theme", wx.YES_NO | wx.ICON_QUESTION, self)
            if answer == wx.YES:
                self._restart()

    def _restart(self) -> None:
        if not self.confirm_discard():
            return
        self._timer.Stop()
        self._save_geometry()
        if getattr(sys, "frozen", False):  # PyInstaller binary
            argv = [sys.executable] + sys.argv[1:]
        else:
            argv = [sys.executable] + sys.argv
        try:
            os.execv(sys.executable, argv)
        except OSError as exc:
            wx.MessageBox(f"Couldn't restart: {exc}\nPlease restart the app manually.",
                          "Theme", wx.OK | wx.ICON_ERROR, self)

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------
    def _last_dir(self) -> str:
        doc = self.editor.doc
        if doc is not None and doc.path is not None:
            return str(doc.path.parent)
        return self.settings_repository.get(LAST_DIR_SETTING_KEY) or str(Path.home())

    def confirm_discard(self) -> bool:
        """True if it's fine to drop the current document (saved, or the
        user chose to save / discard it)."""
        doc = self.editor.doc
        if doc is None or not doc.modified:
            return True
        with wx.MessageDialog(self, f"Save changes to \"{doc.display_name}\" before closing it?",
                              APP_NAME, wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION) as dialog:
            dialog.SetYesNoLabels("&Save", "&Don't save")
            answer = dialog.ShowModal()
        if answer == wx.ID_YES:
            return self.save()
        return answer == wx.ID_NO

    def open_dialog(self) -> None:
        if not self.confirm_discard():
            return
        with wx.FileDialog(self, "Open image", defaultDir=self._last_dir(), wildcard=imageio.OPEN_WILDCARD,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = Path(dialog.GetPath())
        self.open_path(path, confirmed=True)

    def open_path(self, path: Path, confirmed: bool = False) -> None:
        if not confirmed and not self.confirm_discard():
            return
        try:
            doc = Document.open(path)
        except Exception as exc:  # noqa: BLE001 - any unreadable file
            wx.MessageBox(f"Couldn't open {path}:\n{exc}", APP_NAME, wx.OK | wx.ICON_ERROR, self)
            return
        self.settings_repository.set(LAST_DIR_SETTING_KEY, str(path.parent))
        self.editor.set_document(doc)
        self.canvas.reset_view(fit=open_zoom(self.settings_repository) == OPEN_ZOOM_FIT)
        self.SetStatusText(f"Opened {path}", STATUS_HINT)

    def _on_files_dropped(self, files: List[str]) -> None:
        if files:
            self.Raise()
            self.open_path(Path(files[0]))

    def save(self) -> bool:
        doc = self.editor.doc
        if doc is None:
            return False
        if doc.path is None or imageio.save_format_for(doc.path) is None:
            return self.save_as()  # e.g. a .bmp/.webp was opened: pick PNG/JPEG
        return self._write(doc.path)

    def save_as(self) -> bool:
        doc = self.editor.doc
        if doc is None:
            return False
        is_jpeg = doc.source_format == imageio.JPEG
        stem = doc.path.stem if doc.path else "untitled"
        with wx.FileDialog(self, "Save image as", defaultDir=self._last_dir(),
                           defaultFile=stem + (".jpg" if is_jpeg else ".png"), wildcard=imageio.SAVE_WILDCARD,
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialog:
            dialog.SetFilterIndex(1 if is_jpeg else 0)
            if dialog.ShowModal() != wx.ID_OK:
                return False
            chosen = Path(dialog.GetPath())
            path = imageio.with_save_extension(chosen, dialog.GetFilterIndex())
        if path != chosen and path.exists():
            answer = wx.MessageBox(f"{path.name} already exists. Replace it?", APP_NAME,
                                   wx.YES_NO | wx.ICON_WARNING, self)
            if answer != wx.YES:
                return False
        if self._write(path):
            self.settings_repository.set(LAST_DIR_SETTING_KEY, str(path.parent))
            return True
        return False

    def _write(self, path: Path) -> bool:
        doc = self.editor.doc
        busy = wx.BusyCursor()
        try:
            doc.save(path, jpeg_quality(self.settings_repository), png_compress_level(self.settings_repository))
        except Exception as exc:  # noqa: BLE001 - disk full, permissions, bad extension...
            del busy
            wx.MessageBox(f"Couldn't save {path}:\n{exc}", APP_NAME, wx.OK | wx.ICON_ERROR, self)
            return False
        del busy
        self.SetStatusText(f"Saved {path}", STATUS_HINT)
        self._update_title()
        return True

    # ------------------------------------------------------------------
    # Image commands
    # ------------------------------------------------------------------
    def _crop(self) -> None:
        if self.editor.crop_tool.rect is None:
            self.editor.set_tool("crop")
            self.SetStatusText("Drag on the image to choose the area to keep, then press Enter.", STATUS_HINT)
            return
        self.editor.apply_crop()

    def _resize(self) -> None:
        doc = self.editor.doc
        if doc is None:
            return
        with ResizeDialog(self, doc.size) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            width, height, resample = dialog.result()
        try:
            with wx.BusyCursor():
                self.editor.resize(width, height, resample)
        except (ValueError, MemoryError) as exc:
            wx.MessageBox(f"Couldn't resize: {exc}", APP_NAME, wx.OK | wx.ICON_ERROR, self)

    # ------------------------------------------------------------------
    # Editor events -> title, status, settings
    # ------------------------------------------------------------------
    def _on_editor(self, what: str) -> None:
        if what == "document":
            self._update_title()
            self._update_status()
        elif what == "tool":
            self.SetStatusText(self.editor.tool.HINT, STATUS_HINT)
        elif what == "style":
            self.settings_repository.set(DEFAULT_STYLE_SETTING_KEY, json.dumps(self.editor.style.to_dict()))

    def _update_title(self) -> None:
        doc = self.editor.doc
        if doc is None:
            self.SetTitle(APP_NAME)
            return
        self.SetTitle(f"{'*' if doc.modified else ''}{doc.display_name} - {APP_NAME}")

    def _update_status(self) -> None:
        doc = self.editor.doc
        self.SetStatusText(f"{doc.size[0]} × {doc.size[1]} px" if doc else "", STATUS_SIZE)
        self.SetStatusText(f"{self.canvas.viewport.zoom * 100:.0f}%" if doc else "", STATUS_ZOOM)

    def _show_pointer(self, point) -> None:
        self.SetStatusText(f"{point[0]}, {point[1]}" if point else "", STATUS_POINTER)

    def _on_sidebar_collapsed(self, collapsed: bool) -> None:
        self.settings_repository.set_bool(SIDEBAR_COLLAPSED_SETTING_KEY, collapsed)

    # ------------------------------------------------------------------
    # Keyboard: tool letters, Enter/Esc/Delete/arrows, Space (pan), +/-
    # ------------------------------------------------------------------
    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        focus = wx.Window.FindFocus()
        top = focus.GetTopLevelParent() if focus else None
        if event.GetKeyCode() == ord("Y") and event.ControlDown() and not event.ShiftDown() \
                and (top is None or top is self) and not isinstance(focus, _TEXT_ENTRY_TYPES):
            self.editor.redo()  # Windows-style redo, alongside Ctrl+Shift+Z
            return
        if (top is not None and top is not self) or isinstance(focus, _TEXT_ENTRY_TYPES) \
                or event.ControlDown() or event.AltDown() or event.MetaDown():
            event.Skip()  # dialogs, typing in a field, menu accelerators
            return
        code = event.GetKeyCode()
        name = _KEY_NAMES.get(code)
        if name and self.editor.doc is not None and self.editor.key(name, Mods(shift=event.ShiftDown())):
            return
        if code == wx.WXK_SPACE:
            if self.editor.doc is not None:
                self.canvas.SetFocus()
                self.canvas.set_space_down(True)
            return  # never let Space "click" a focused sidebar button
        char = chr(event.GetUnicodeKey()) if event.GetUnicodeKey() != wx.WXK_NONE else ""
        if char in ("+", "="):
            self.canvas.zoom_step(+1)
            return
        if char == "-":
            self.canvas.zoom_step(-1)
            return
        for cls in TOOL_CLASSES:
            if char.upper() == cls.KEY:
                self.editor.set_tool(cls.NAME)
                return
        event.Skip()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self) -> None:
        menu_bar = wx.MenuBar()
        ed = self.editor
        has_doc = lambda: ed.doc is not None  # noqa: E731

        def item(menu: wx.Menu, label: str, handler, enabled=None, help_text: str = "", id_=wx.ID_ANY):
            entry = menu.Append(id_, label, help_text)
            self.Bind(wx.EVT_MENU, lambda evt: handler(), entry)
            if enabled is not None:
                self.Bind(wx.EVT_UPDATE_UI, lambda evt: evt.Enable(bool(enabled())), entry)
            return entry

        file_menu = wx.Menu()
        item(file_menu, "&Open...\tCtrl+O", self.open_dialog, id_=wx.ID_OPEN)
        item(file_menu, "&Save\tCtrl+S", self.save, has_doc, id_=wx.ID_SAVE)
        item(file_menu, "Save &As...\tCtrl+Shift+S", self.save_as, has_doc, id_=wx.ID_SAVEAS)
        file_menu.AppendSeparator()
        item(file_menu, "Preferences...\tCtrl+,", self._on_preferences, id_=wx.ID_PREFERENCES)
        file_menu.AppendSeparator()
        item(file_menu, "Exit\tAlt+F4", self.Close, id_=wx.ID_EXIT)

        edit_menu = wx.Menu()
        # Enabled state and "Undo Move"-style labels: _update_undo_redo.
        self._undo_item = item(edit_menu, "&Undo\tCtrl+Z", ed.undo, id_=wx.ID_UNDO)
        self._redo_item = item(edit_menu, "&Redo\tCtrl+Shift+Z", ed.redo, id_=wx.ID_REDO)
        edit_menu.AppendSeparator()
        has_sel = lambda: bool(ed.selection)  # noqa: E731
        # Delete has no accelerator on purpose: it would steal the key from
        # text fields. The frame's key hook handles Delete/Backspace instead.
        item(edit_menu, "&Delete Shape", ed.delete_selection, has_sel, "Delete the selected shapes (Del)")
        item(edit_menu, "D&uplicate\tCtrl+D", ed.duplicate_selection, has_sel)
        item(edit_menu, "Select &All Shapes\tCtrl+A", ed.select_all, lambda: ed.doc and ed.doc.shapes)
        edit_menu.AppendSeparator()
        item(edit_menu, "Bring to &Front\tCtrl+]", lambda: ed.reorder_selection(True), has_sel)
        item(edit_menu, "Send to &Back\tCtrl+[", lambda: ed.reorder_selection(False), has_sel)

        image_menu = wx.Menu()
        item(image_menu, "&Crop to Selection\tCtrl+Shift+X", self._crop, has_doc,
             "Crop to the crop tool's rectangle (picks the crop tool if there is none)")
        item(image_menu, "&Resize...\tCtrl+R", self._resize, has_doc)
        image_menu.AppendSeparator()
        item(image_menu, "&Flatten Shapes", ed.flatten, lambda: ed.doc and ed.doc.shapes,
             "Draw the shapes into the image pixels (they stop being editable)")

        view_menu = wx.Menu()
        item(view_menu, "Zoom &In\tCtrl+=", lambda: self.canvas.zoom_step(+1), has_doc)
        item(view_menu, "Zoom &Out\tCtrl+-", lambda: self.canvas.zoom_step(-1), has_doc)
        item(view_menu, "&Fit to Window\tCtrl+0", self.canvas.zoom_fit, has_doc)
        item(view_menu, "&Actual Size\tCtrl+1", self.canvas.zoom_actual, has_doc)
        view_menu.AppendSeparator()
        item(view_menu, "Collapse / Expand &Sidebar\tCtrl+B",
             lambda: self.sidebar.set_collapsed(not self.sidebar.collapsed))

        tools_menu = wx.Menu()
        for cls in TOOL_CLASSES:
            # The letter is in the label, not an accelerator: plain-letter
            # accelerators would fire while typing (the key hook handles it).
            entry = tools_menu.AppendRadioItem(wx.ID_ANY, f"{cls.LABEL}   ({cls.KEY})", cls.HINT)
            self.Bind(wx.EVT_MENU, lambda evt, name=cls.NAME: ed.set_tool(name), entry)
            self.Bind(wx.EVT_UPDATE_UI, lambda evt, name=cls.NAME: evt.Check(ed.tool.NAME == name), entry)

        help_menu = wx.Menu()
        item(help_menu, "About...", self._on_about, id_=wx.ID_ABOUT)

        menu_bar.Append(file_menu, "&File")
        menu_bar.Append(edit_menu, "&Edit")
        menu_bar.Append(image_menu, "&Image")
        menu_bar.Append(view_menu, "&View")
        menu_bar.Append(tools_menu, "&Tools")
        menu_bar.Append(help_menu, "&Help")
        self.SetMenuBar(menu_bar)
        self.Bind(wx.EVT_UPDATE_UI, lambda e: self._update_undo_redo(e, redo=False), self._undo_item)
        self.Bind(wx.EVT_UPDATE_UI, lambda e: self._update_undo_redo(e, redo=True), self._redo_item)

    def _update_undo_redo(self, event: wx.UpdateUIEvent, redo: bool) -> None:
        history = self.editor.doc.history if self.editor.doc else None
        name = (history.redo_label() if redo else history.undo_label()) if history else None
        event.Enable(bool(name))
        base, accel = ("&Redo", "Ctrl+Shift+Z") if redo else ("&Undo", "Ctrl+Z")
        label = f"{base} {name}\t{accel}" if name else f"{base}\t{accel}"
        menu_item = self._redo_item if redo else self._undo_item
        if menu_item.GetItemLabel() != label:
            menu_item.SetItemLabel(label)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_close(self, event: wx.CloseEvent) -> None:
        if event.CanVeto() and not self.confirm_discard():
            event.Veto()
            return
        self._timer.Stop()
        self._save_geometry()
        self.Destroy()

    def _on_about(self) -> None:
        with AboutDialog(self) as dialog:
            dialog.ShowModal()

    def _on_preferences(self) -> None:
        with PreferencesDialog(self, self.settings_repository, on_saved=self._on_prefs_saved) as dialog:
            dialog.ShowModal()
