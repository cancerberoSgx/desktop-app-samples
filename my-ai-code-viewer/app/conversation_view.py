import shlex
import subprocess
import sys
from typing import Callable, Optional, Set

import wx
import wx.html

from app.conversation_html import DARK, LIGHT, render_conversation_html
from app.models import Conversation
from app.providers import PROVIDER_LABELS
from app.repositories import (RENDER_MARKDOWN_SETTING_KEY, SHOW_THINKING_SETTING_KEY, SHOW_TOOLS_SETTING_KEY,
                              IndexRepository, SettingsRepository)

RESUME_COMMANDS = {
    "claude-code": "claude --resume {id}",
    "opencode": "opencode --session {id}",
}


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

        toolbar = wx.WrapSizer(wx.HORIZONTAL)
        self._expand_tools = wx.CheckBox(self, label="Expand tool calls")
        self._expand_tools.SetValue(self._settings.get_bool(SHOW_TOOLS_SETTING_KEY))
        self._expand_thinking = wx.CheckBox(self, label="Expand thinking")
        self._expand_thinking.SetValue(self._settings.get_bool(SHOW_THINKING_SETTING_KEY))
        self._render_markdown = wx.CheckBox(self, label="Render markdown")
        self._render_markdown.SetValue(self._settings.get_bool(RENDER_MARKDOWN_SETTING_KEY, default=True))
        self._render_markdown.SetToolTip("Render assistant replies and thinking as markdown "
                                         "(off: plain text with code blocks)")
        self._copy_resume_btn = wx.Button(self, label="Copy resume cmd")
        self._copy_id_btn = wx.Button(self, label="Copy id")
        self._open_folder_btn = wx.Button(self, label="Open folder")
        self._open_source_btn = wx.Button(self, label="Open file")
        for ctrl in (self._expand_tools, self._expand_thinking, self._render_markdown):
            toolbar.Add(ctrl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        for btn in (self._copy_resume_btn, self._copy_id_btn, self._open_folder_btn, self._open_source_btn):
            toolbar.Add(btn, 0, wx.RIGHT | wx.BOTTOM, 4)
        self._copy_resume_btn.SetToolTip("Copy a shell command that reopens this conversation in its CLI")
        self._open_source_btn.SetToolTip("Open the raw transcript file / database")

        self._html = _Html(self, self._on_link)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(self._html, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self._expand_tools.Bind(wx.EVT_CHECKBOX, self._on_toggle_defaults)
        self._expand_thinking.Bind(wx.EVT_CHECKBOX, self._on_toggle_defaults)
        self._render_markdown.Bind(wx.EVT_CHECKBOX, self._on_toggle_markdown)
        self._copy_resume_btn.Bind(wx.EVT_BUTTON, self._on_copy_resume)
        self._copy_id_btn.Bind(wx.EVT_BUTTON, self._on_copy_id)
        self._open_folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        self._open_source_btn.Bind(wx.EVT_BUTTON, self._on_open_source)

        self.clear()

    # ------------------------------------------------------------------
    def clear(self, message: str = "Select a conversation to read it.") -> None:
        self._conversation = None
        self._blocks = []
        self._expanded = set()
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
        self._render(keep_scroll=keep_scroll)
        self._update_buttons()

    # ------------------------------------------------------------------
    def _palette(self):
        try:
            dark = wx.SystemSettings.GetAppearance().IsDark()
        except AttributeError:
            dark = False
        return DARK if dark else LIGHT

    def _render(self, keep_scroll: bool = False) -> None:
        if self._conversation is None:
            return
        scroll = self._html.GetViewStart() if keep_scroll else (0, 0)
        page = render_conversation_html(
            self._conversation, self._blocks, project_path=self._project_path,
            provider_label=PROVIDER_LABELS.get(self._conversation.provider, self._conversation.provider),
            expanded=self._expanded, expand_tools=self._expand_tools.GetValue(),
            expand_thinking=self._expand_thinking.GetValue(),
            render_markdown=self._render_markdown.GetValue(), pal=self._palette(),
        )
        self._html.Freeze()
        try:
            self._html.SetPage(page)
            self._html.Scroll(*scroll)
        finally:
            self._html.Thaw()

    def _update_buttons(self) -> None:
        conv = self._conversation
        has = conv is not None
        self._copy_resume_btn.Enable(has and conv.provider in RESUME_COMMANDS and not conv.parent_external_id)
        self._copy_id_btn.Enable(has)
        self._open_folder_btn.Enable(has and bool(self._project_path))
        self._open_source_btn.Enable(has and bool(conv.source_path) and not conv.missing_from_source)

    def _on_link(self, href: str) -> None:
        if href.startswith("toggle:"):
            seq = int(href.split(":", 1)[1])
            self._expanded ^= {seq}
            self._render(keep_scroll=True)
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

    def _on_copy_resume(self, event: wx.CommandEvent) -> None:
        conv = self._conversation
        if conv:
            cmd = RESUME_COMMANDS[conv.provider].format(id=conv.external_id)
            self._copy(f"cd {_shell_quote(self._project_path)} && {cmd}" if self._project_path else cmd)

    def _on_copy_id(self, event: wx.CommandEvent) -> None:
        if self._conversation:
            self._copy(self._conversation.external_id)

    def _on_open_folder(self, event: wx.CommandEvent) -> None:
        _open_path(self._project_path)

    def _on_open_source(self, event: wx.CommandEvent) -> None:
        if self._conversation and self._conversation.source_path:
            _open_path(self._conversation.source_path)


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
