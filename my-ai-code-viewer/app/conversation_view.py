import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import wx
import wx.html

from app.conversation_export import ExportOptions, render_export_html
from app import theme
from app.conversation_html import DARK, LIGHT, Palette, outline_item_html, render_conversation_html
from app.conversation_turns import Turn, split_turns
from app.models import Conversation
from app.providers import PROVIDER_LABELS
from app.version import get_version
from app.repositories import (EXPORT_DIR_SETTING_KEY, OUTLINE_SASH_SETTING_KEY, QA_ONLY_SETTING_KEY, RENDER_MARKDOWN_SETTING_KEY,
                              SHOW_OUTLINE_SETTING_KEY, SHOW_THINKING_SETTING_KEY, SHOW_TOOLS_SETTING_KEY,
                              IndexRepository, SettingsRepository)

# Viewer options: (setting key, label, default, tooltip). Shown both as
# toolbar checkboxes and on the Conversation tab of File > Preferences.
CONVERSATION_OPTIONS = (
    (SHOW_OUTLINE_SETTING_KEY, "Outline", False,
     "List of prompts and answers; click one to jump to it"),
    (QA_ONLY_SETTING_KEY, "Q&A only", True,
     "Show only each prompt and its final answer (hide tool calls, thinking and intermediate replies)"),
    (SHOW_TOOLS_SETTING_KEY, "Expand tool calls", False,
     "Show tool calls and results expanded instead of collapsed"),
    (SHOW_THINKING_SETTING_KEY, "Expand thinking", False,
     "Show thinking blocks expanded instead of collapsed"),
    (RENDER_MARKDOWN_SETTING_KEY, "Render markdown", True,
     "Render assistant replies and thinking as markdown (off: plain text with code blocks)"),
)



@dataclass(frozen=True)
class ConversationAction:
    """One action on the open conversation - shown in the view's "Actions"
    dropdown and in the frame's Conversation menu."""
    label: str           # menu label (may include "\tAccel")
    help: str
    run: Callable[[], None]
    enabled: Callable[[], bool]
    separator_before: bool = False


RESUME_COMMANDS = {
    "claude-code": "claude --resume {id}",
    "opencode": "opencode --session {id}",
    "codex": "codex resume {id}",
    "gemini": "gemini --resume {id}",
}


class _Outline(wx.html.SimpleHtmlListBox):
    """Keeps the rows' own gray text colours when selected (the default
    repaints every selected row in the system highlight text colour); the
    selected background is the palette's `outline_sel_bg`."""

    def GetSelectedTextColour(self, colFg):
        return colFg


class _Html(wx.html.HtmlWindow):
    def __init__(self, parent: wx.Window, on_link: Callable[[str], None]) -> None:
        super().__init__(parent, style=wx.html.HW_SCROLLBAR_AUTO)
        self._on_link = on_link
        if "gtk" in wx.PlatformInfo:
            self.SetStandardFonts()

    def OnLinkClicked(self, link: wx.html.HtmlLinkInfo) -> None:  # noqa: N802 - wx override
        self._on_link(link.GetHref())


class ConversationView(wx.Panel):
    """Right-hand pane of the Projects page: toolbar + rendered transcript."""

    def __init__(self, parent: wx.Window, index_repository: IndexRepository,
                 settings_repository: SettingsRepository) -> None:
        super().__init__(parent)
        self._index = index_repository
        self._settings = settings_repository
        self._conversation: Optional[Conversation] = None
        self._project_path = ""
        self._blocks = []
        self._expanded: Set[int] = set()
        self._turns: List[Turn] = []
        # href ("turn:3" / "answer:3") -> y in page pixels; None = stale.
        self._positions: Optional[Dict[str, int]] = None
        self._dark = self._want_dark()

        toolbar = wx.WrapSizer(wx.HORIZONTAL)
        self._option_boxes: Dict[str, wx.CheckBox] = {}
        for key, label, default, tooltip in CONVERSATION_OPTIONS:
            box = wx.CheckBox(self, label=label.replace("&", "&&"))
            box.SetValue(self._settings.get_bool(key, default=default))
            box.SetToolTip(tooltip)
            self._option_boxes[key] = box
        self._show_outline = self._option_boxes[SHOW_OUTLINE_SETTING_KEY]
        self._qa_only = self._option_boxes[QA_ONLY_SETTING_KEY]
        self._expand_tools = self._option_boxes[SHOW_TOOLS_SETTING_KEY]
        self._expand_thinking = self._option_boxes[SHOW_THINKING_SETTING_KEY]
        self._render_markdown = self._option_boxes[RENDER_MARKDOWN_SETTING_KEY]
        self.actions: List[ConversationAction] = self._build_actions()
        self._actions_btn = wx.Button(self, label="Actions \u25be")
        self._actions_btn.SetToolTip("Actions on this conversation (also in the Conversation menu)")
        for ctrl in self._option_boxes.values():
            toolbar.Add(ctrl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        toolbar.Add(self._actions_btn, 0, wx.RIGHT | wx.BOTTOM, 4)

        self._split = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        self._split.SetMinimumPaneSize(120)
        self._outline = _Outline(self._split)
        self._outline.SetMargins(4, 4)
        self._html = _Html(self._split, self._on_link)
        self._apply_widget_colours()
        self._split.SplitVertically(self._outline, self._html,
                                    self._settings.get_int(OUTLINE_SASH_SETTING_KEY) or 260)
        if not self._show_outline.GetValue():
            self._split.Unsplit(self._outline)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(self._split, 1, wx.EXPAND)
        self.SetSizer(sizer)

        # Follow the reading position in the outline. Polling the scroll
        # offset is the one signal that covers scrollbar, wheel and keys.
        self._last_view_y = -1
        self._follow_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_follow_timer, self._follow_timer)
        self._follow_timer.Start(250)
        self._html.Bind(wx.EVT_SIZE, self._on_html_size)
        self._outline.Bind(wx.EVT_LISTBOX, self._on_outline_select)
        self._split.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self._on_outline_sash)
        self._show_outline.Bind(wx.EVT_CHECKBOX, self._on_toggle_outline)
        self._qa_only.Bind(wx.EVT_CHECKBOX, self._on_toggle_qa)

        self._expand_tools.Bind(wx.EVT_CHECKBOX, self._on_toggle_defaults)
        self._expand_thinking.Bind(wx.EVT_CHECKBOX, self._on_toggle_defaults)
        self._render_markdown.Bind(wx.EVT_CHECKBOX, self._on_toggle_markdown)
        self._actions_btn.Bind(wx.EVT_BUTTON, self._on_actions_button)

        self.clear()

    # ------------------------------------------------------------------
    def clear(self, message: str = "Select a conversation to read it.") -> None:
        self._conversation = None
        self._blocks = []
        self._expanded = set()
        self._turns = []
        self._outline.Set([])
        self._positions = {}
        pal = self._palette()
        self._html.SetPage(f'<html><body bgcolor="{pal.bg}" text="{pal.muted}"><p>{message}</p></body></html>')
        self._update_buttons()

    def current_conversation_id(self) -> Optional[int]:
        return self._conversation.id if self._conversation else None

    def show_conversation(self, conversation_id: int, project_path: str, keep_scroll: bool = False) -> None:
        conv = self._index.get_conversation(conversation_id)
        if conv is None:
            self.clear("Conversation not found.")
            return
        if not keep_scroll:
            self._expanded = set()
        self._conversation = conv
        self._project_path = project_path
        self._blocks = self._index.list_messages(conversation_id)
        self._turns = split_turns(self._blocks)
        self._fill_outline()
        self._render(keep_scroll=keep_scroll)
        self._update_buttons()

    # ------------------------------------------------------------------
    def _want_dark(self) -> bool:
        value = theme.normalize(self._settings.get(theme.THEME_SETTING_KEY))
        return theme.resolve_dark(value, theme.system_is_dark() if value == theme.SYSTEM else False)

    def _palette(self) -> Palette:
        return DARK if self._dark else LIGHT

    def _apply_widget_colours(self) -> None:
        # The outline's own background is native; match it to the palette so
        # a forced theme (before the restart that themes native widgets)
        # doesn't put dark-palette text on a light list or vice versa.
        pal = self._palette()
        self._outline.SetSelectionBackground(wx.Colour(pal.outline_sel_bg))
        for ctrl in (self._outline, self._html):
            ctrl.SetBackgroundColour(wx.Colour(pal.bg))
            ctrl.Refresh()

    def refresh_theme(self) -> None:
        """Re-render if the effective theme changed (setting saved, or the
        desktop switched light/dark while on "System")."""
        dark = self._want_dark()
        if dark == self._dark:
            return
        self._dark = dark
        self._apply_widget_colours()
        if self._conversation is None:
            self.clear()
            return
        self._fill_outline()
        self._render(keep_scroll=True)

    def _render(self, keep_scroll: bool = False) -> None:
        if self._conversation is None:
            return
        scroll = self._html.GetViewStart() if keep_scroll else (0, 0)
        page = render_conversation_html(
            self._conversation, self._blocks, project_path=self._project_path,
            provider_label=PROVIDER_LABELS.get(self._conversation.provider, self._conversation.provider),
            expanded=self._expanded, expand_tools=self._expand_tools.GetValue(),
            expand_thinking=self._expand_thinking.GetValue(),
            render_markdown=self._render_markdown.GetValue(), qa_only=self._qa_only.GetValue(),
            pal=self._palette(),
        )
        self._html.Freeze()
        try:
            self._html.SetPage(page)
            self._html.Scroll(*scroll)
        finally:
            self._html.Thaw()
        self._positions = None
        self._last_view_y = -1

    # ------------------------------------------------------------- outline
    def _fill_outline(self) -> None:
        pal = self._palette()
        selected = self._outline.GetSelection()
        self._outline.Freeze()
        try:
            # Set(), not Clear()+Append(): Clear() resolves to VListBox's,
            # which drops the row count but keeps the old strings.
            self._outline.Set([outline_item_html(t, pal) for t in self._turns])
            if 0 <= selected < len(self._turns):
                self._outline.SetSelection(selected)
        finally:
            self._outline.Thaw()

    def _anchor_positions(self) -> Dict[str, int]:
        """Page y of each turn:/answer: link. wx.html has no public anchor
        lookup, so walk the laid-out cells summing relative offsets."""
        if self._positions is None:
            found: Dict[str, int] = {}
            stack = [(self._html.GetInternalRepresentation(), 0)]
            while stack:
                cell, offset = stack.pop()
                while cell is not None:
                    y = offset + cell.GetPosY()
                    link = cell.GetLink()
                    if link is not None:
                        href = link.GetHref()
                        if href.startswith(("turn:", "answer:")) and href not in found:
                            found[href] = y
                    child = cell.GetFirstChild()
                    if child is not None:
                        stack.append((child, y))
                    cell = cell.GetNext()
            self._positions = found
        return self._positions

    def _scroll_to(self, href: str) -> None:
        y = self._anchor_positions().get(href)
        if y is None:
            return
        _, ppu = self._html.GetScrollPixelsPerUnit()
        self._html.Scroll(-1, max(0, y - 30) // max(ppu, 1))

    def _current_turn_index(self) -> int:
        _, ppu = self._html.GetScrollPixelsPerUnit()
        top = self._html.GetViewStart()[1] * max(ppu, 1)
        positions = self._anchor_positions()
        current = 0
        for i, turn in enumerate(self._turns):
            y = positions.get(f"turn:{turn.number}")
            if y is not None and y <= top + 60:
                current = i
        return current

    def _on_follow_timer(self, event: wx.TimerEvent) -> None:
        if not self._turns or not self._split.IsSplit() or not self.IsShownOnScreen():
            return
        view_y = self._html.GetViewStart()[1]
        if view_y == self._last_view_y:
            return
        self._last_view_y = view_y
        index = self._current_turn_index()
        if index != self._outline.GetSelection():
            self._outline.SetSelection(index)
            if not self._outline.IsRowVisible(index):
                self._outline.ScrollToRow(index)

    def _on_html_size(self, event: wx.SizeEvent) -> None:
        self._positions = None  # text re-wraps on resize
        self._last_view_y = -1
        event.Skip()

    def _on_outline_select(self, event: wx.CommandEvent) -> None:
        index = self._outline.GetSelection()
        if 0 <= index < len(self._turns):
            self._scroll_to(f"turn:{self._turns[index].number}")
            self._last_view_y = self._html.GetViewStart()[1]

    def _select_outline_turn(self, number: int) -> None:
        index = number - 1
        if 0 <= index < len(self._turns) and self._split.IsSplit():
            self._outline.SetSelection(index)
            if not self._outline.IsRowVisible(index):
                self._outline.ScrollToRow(index)

    def apply_settings(self) -> None:
        """Re-read the viewer options from settings (after File > Preferences
        changed them) and update the toolbar + rendering to match."""
        self.refresh_theme()
        changed = False
        for key, _label, default, _tooltip in CONVERSATION_OPTIONS:
            box = self._option_boxes[key]
            value = self._settings.get_bool(key, default=default)
            if box.GetValue() != value:
                box.SetValue(value)
                changed = True
        if not changed:
            return
        self._apply_outline_visibility()
        self._expanded = set()
        index = self._current_turn_index() if self._turns else -1
        self._render()
        if 0 <= index < len(self._turns):
            self._scroll_to(f"turn:{self._turns[index].number}")

    def _on_toggle_outline(self, event: wx.CommandEvent) -> None:
        self._settings.set_bool(SHOW_OUTLINE_SETTING_KEY, self._show_outline.GetValue())
        self._apply_outline_visibility()

    def _apply_outline_visibility(self) -> None:
        show = self._show_outline.GetValue()
        if show and not self._split.IsSplit():
            self._split.SplitVertically(self._outline, self._html,
                                        self._settings.get_int(OUTLINE_SASH_SETTING_KEY) or 260)
            self._last_view_y = -1
        elif not show and self._split.IsSplit():
            self._split.Unsplit(self._outline)

    def _on_toggle_qa(self, event: wx.CommandEvent) -> None:
        self._settings.set_bool(QA_ONLY_SETTING_KEY, self._qa_only.GetValue())
        # Keep the reader on the same turn across the switch.
        index = self._current_turn_index() if self._turns else -1
        self._render()
        if 0 <= index < len(self._turns):
            self._scroll_to(f"turn:{self._turns[index].number}")

    def _on_outline_sash(self, event: wx.SplitterEvent) -> None:
        if self._split.IsSplit():
            self._settings.set_int(OUTLINE_SASH_SETTING_KEY, self._split.GetSashPosition())
        event.Skip()

    # ------------------------------------------------------------ actions
    def _build_actions(self) -> List[ConversationAction]:
        def conv() -> Optional[Conversation]:
            return self._conversation

        return [
            ConversationAction(
                "Copy resume command", "Copy a shell command that reopens this conversation in its CLI",
                self.copy_resume_command,
                lambda: conv() is not None and conv().provider in RESUME_COMMANDS and not conv().parent_external_id),
            ConversationAction("Copy id", "Copy the conversation's session id",
                               self.copy_id, lambda: conv() is not None),
            ConversationAction("Open project folder", "Open the project folder in the file manager",
                               self.open_folder, lambda: conv() is not None and bool(self._project_path),
                               separator_before=True),
            ConversationAction("Open source file", "Open the raw transcript file / database",
                               self.open_source,
                               lambda: conv() is not None and bool(conv().source_path)
                               and not conv().missing_from_source),
            ConversationAction("Export as HTML...\tCtrl+S",
                               "Save this conversation as an interactive, self-contained HTML page",
                               self.export_html, lambda: conv() is not None, separator_before=True),
        ]

    def _update_buttons(self) -> None:
        self._actions_btn.Enable(self._conversation is not None)

    def _on_actions_button(self, event: wx.CommandEvent) -> None:
        menu = wx.Menu()
        for action in self.actions:
            if action.separator_before and menu.GetMenuItemCount():
                menu.AppendSeparator()
            item = menu.Append(wx.ID_ANY, action.label.split("\t", 1)[0], action.help)
            item.Enable(action.enabled())
            menu.Bind(wx.EVT_MENU, lambda evt, a=action: a.run(), item)
        btn = self._actions_btn
        btn.PopupMenu(menu, wx.Point(0, btn.GetSize().height))
        menu.Destroy()

    def _on_link(self, href: str) -> None:
        if href.startswith("toggle:"):
            seq = int(href.split(":", 1)[1])
            self._expanded ^= {seq}
            self._render(keep_scroll=True)
        elif href.startswith(("turn:", "answer:")):
            self._select_outline_turn(int(href.split(":", 1)[1]))
        else:
            wx.LaunchDefaultBrowser(href)

    def _on_toggle_defaults(self, event: wx.CommandEvent) -> None:
        self._settings.set_bool(SHOW_TOOLS_SETTING_KEY, self._expand_tools.GetValue())
        self._settings.set_bool(SHOW_THINKING_SETTING_KEY, self._expand_thinking.GetValue())
        self._expanded = set()
        self._render(keep_scroll=True)

    def _on_toggle_markdown(self, event: wx.CommandEvent) -> None:
        self._settings.set_bool(RENDER_MARKDOWN_SETTING_KEY, self._render_markdown.GetValue())
        self._render(keep_scroll=True)

    def _copy(self, text: str) -> None:
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
        top = self.GetTopLevelParent()
        if isinstance(top, wx.Frame) and top.GetStatusBar():
            top.SetStatusText(f"Copied: {text}", 0)

    def copy_resume_command(self) -> None:
        conv = self._conversation
        if conv:
            cmd = RESUME_COMMANDS[conv.provider].format(id=conv.external_id)
            self._copy(f"cd {_shell_quote(self._project_path)} && {cmd}" if self._project_path else cmd)

    def copy_id(self) -> None:
        if self._conversation:
            self._copy(self._conversation.external_id)

    # ------------------------------------------------------------- export
    def export_options(self) -> ExportOptions:
        """An export opens with the viewer's current toggles and theme."""
        return ExportOptions(
            show_outline=self._show_outline.GetValue(), qa_only=self._qa_only.GetValue(),
            expand_tools=self._expand_tools.GetValue(), expand_thinking=self._expand_thinking.GetValue(),
            render_markdown=self._render_markdown.GetValue(),
            theme=theme.normalize(self._settings.get(theme.THEME_SETTING_KEY)),
        )

    def expanded_blocks(self, conversation_id: int) -> Set[int]:
        """Blocks the user flipped in the open conversation (empty for others)."""
        if self._conversation is not None and self._conversation.id == conversation_id:
            return set(self._expanded)
        return set()

    def export_html(self) -> None:
        """Save the open conversation (plus its subagents) as a standalone
        HTML page that starts with the viewer's current toggles."""
        conv = self._conversation
        if conv is None:
            return
        start_dir = self._settings.get(EXPORT_DIR_SETTING_KEY) or ""
        with wx.FileDialog(self, "Export conversation as HTML", defaultDir=start_dir,
                           defaultFile=export_filename(conv.display_title), wildcard="HTML files (*.html)|*.html",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = dialog.GetPath()
        if not path.lower().endswith((".html", ".htm")):
            path += ".html"
        subagents = [] if conv.parent_external_id else [
            (c, self._index.list_messages(c.id))
            for c in sorted(self._index.list_conversations([conv.project_id]),
                            key=lambda c: c.started_at or "")
            if c.parent_external_id == conv.external_id and c.provider == conv.provider
        ]
        options = self.export_options()
        try:
            with wx.BusyCursor():
                page = render_export_html(
                    conv, self._blocks, project_path=self._project_path,
                    provider_label=PROVIDER_LABELS.get(conv.provider, conv.provider), options=options,
                    expanded=self._expanded, subagents=subagents, app_version=get_version(),
                )
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(page)
        except OSError as exc:
            wx.MessageBox(f"Couldn't write {path}:\n{exc}", "Export", wx.OK | wx.ICON_ERROR)
            return
        self._settings.set(EXPORT_DIR_SETTING_KEY, os.path.dirname(path))
        top = self.GetTopLevelParent()
        if isinstance(top, wx.Frame) and top.GetStatusBar():
            top.SetStatusText(f"Exported to {path}", 0)
        if wx.MessageBox(f"Exported to\n{path}\n\nOpen it in the browser?", "Export",
                         wx.YES_NO | wx.ICON_INFORMATION, self) == wx.YES:
            wx.LaunchDefaultBrowser(Path(path).resolve().as_uri())

    def open_folder(self) -> None:
        _open_path(self._project_path)

    def open_source(self) -> None:
        if self._conversation and self._conversation.source_path:
            _open_path(self._conversation.source_path)


def export_filename(title: str) -> str:
    """A filesystem-safe default file name for an exported conversation."""
    slug = re.sub(r"[^\w.-]+", "-", title.strip()).strip("-.")[:80].rstrip("-.")
    return f"{slug or 'conversation'}.html"


def _shell_quote(path: str) -> str:
    return shlex.quote(path)


def _open_path(path: str) -> None:
    """Open a file/folder with the desktop's default handler."""
    if not path:
        return
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            wx.LaunchDefaultApplication(path)
    except OSError as exc:
        wx.MessageBox(f"Couldn't open {path}:\n{exc}", "Open", wx.OK | wx.ICON_ERROR)
