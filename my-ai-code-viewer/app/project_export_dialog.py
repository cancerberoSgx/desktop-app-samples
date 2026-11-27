"""Projects > Export conversations as HTML: pick which conversations go in."""
from typing import List, Sequence

import wx

from app.formatting import format_datetime
from app.models import Conversation
from app.providers import PROVIDER_LABELS

_COLUMNS = (("Conversation", 380, wx.LIST_FORMAT_LEFT), ("Agent", 100, wx.LIST_FORMAT_LEFT),
            ("Last activity", 130, wx.LIST_FORMAT_LEFT), ("Prompts", 70, wx.LIST_FORMAT_RIGHT))


class ProjectExportDialog(wx.Dialog):
    """Checkable list of a project's top-level conversations (subagents are
    exported with their parent), all checked by default."""

    def __init__(self, parent: wx.Window, project_name: str, conversations: Sequence[Conversation]) -> None:
        super().__init__(parent, title="Export conversations as HTML",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._conversations = list(conversations)

        intro = wx.StaticText(self, label=f"Conversations of {project_name} to include in the HTML page:")
        self._list = wx.ListCtrl(self, style=wx.LC_REPORT)
        self._list.EnableCheckBoxes(True)
        for i, (label, width, fmt) in enumerate(_COLUMNS):
            self._list.InsertColumn(i, label, fmt, width)
        for i, c in enumerate(self._conversations):
            title = c.display_title + ("  (archived)" if c.missing_from_source else "")
            self._list.InsertItem(i, title)
            self._list.SetItem(i, 1, PROVIDER_LABELS.get(c.provider, c.provider))
            self._list.SetItem(i, 2, format_datetime(c.updated_at or c.started_at))
            self._list.SetItem(i, 3, str(c.user_message_count))
            self._list.CheckItem(i, True)

        all_btn = wx.Button(self, label="Select all")
        none_btn = wx.Button(self, label="Select none")
        self._count = wx.StaticText(self)
        tools = wx.BoxSizer(wx.HORIZONTAL)
        tools.Add(all_btn, 0, wx.RIGHT, 6)
        tools.Add(none_btn, 0, wx.RIGHT, 12)
        tools.Add(self._count, 0, wx.ALIGN_CENTER_VERTICAL)

        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self._ok = self.FindWindow(wx.ID_OK)
        self._ok.SetLabel("Export...")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(intro, 0, wx.ALL, 10)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(tools, 0, wx.ALL, 10)
        sizer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.SetSizer(sizer)
        self.SetSize(self.FromDIP(wx.Size(760, 520)))
        self.CentreOnParent()

        all_btn.Bind(wx.EVT_BUTTON, lambda evt: self._check_all(True))
        none_btn.Bind(wx.EVT_BUTTON, lambda evt: self._check_all(False))
        self._list.Bind(wx.EVT_LIST_ITEM_CHECKED, lambda evt: self._update_count())
        self._list.Bind(wx.EVT_LIST_ITEM_UNCHECKED, lambda evt: self._update_count())
        # Double-click / Enter on a row toggles it.
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED,
                        lambda evt: self._list.CheckItem(evt.GetIndex(), not self._list.IsItemChecked(evt.GetIndex())))
        self._update_count()

    def _check_all(self, checked: bool) -> None:
        for i in range(self._list.GetItemCount()):
            self._list.CheckItem(i, checked)
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.selected())
        self._count.SetLabel(f"{n} of {len(self._conversations)} selected")
        self._ok.Enable(n > 0)

    def selected(self) -> List[Conversation]:
        """Checked conversations, in list order."""
        return [c for i, c in enumerate(self._conversations) if self._list.IsItemChecked(i)]
