from typing import Callable, Dict, List, Optional

import wx
import wx.dataview as dv

from app.conversation_view import ConversationView
from app.formatting import format_cost, format_datetime, format_tokens
from app.models import Conversation, ProjectGroup
from app.providers import PROVIDER_LABELS
from app.repositories import (
    LAST_CONVERSATION_SETTING_KEY,
    LAST_PROJECT_SETTING_KEY,
    PROVIDER_FILTER_SETTING_KEY,
    IndexRepository,
    SettingsRepository,
)

PROJECT_SASH_SETTING_KEY = "projects_sash"
CONVERSATION_SASH_SETTING_KEY = "conversations_sash"

# (label, width, sortable numerically)
CONVERSATION_COLUMNS = [
    ("Conversation", 240, False),
    ("Tool", 90, False),
    ("Last activity", 125, False),
    ("Prompts", 65, True),
    ("Tool calls", 75, True),
    ("Tokens", 70, True),
    ("Cost", 65, True),
]


class _ConversationComparator(dv.TreeListItemComparator):
    """Numeric-aware sorting for the conversations tree (the default
    comparator sorts every column as text, so '9' > '10')."""

    def __init__(self, page: "ProjectsPage") -> None:
        super().__init__()
        self._page = page

    def Compare(self, treelist, first, second, column):  # noqa: N802 - wx override
        a = self._page._conversation_for_item(first)
        b = self._page._conversation_for_item(second)
        if a is None or b is None:
            return 0
        key = _sort_key(a, column)
        other = _sort_key(b, column)
        return (key > other) - (key < other)


def _sort_key(conv: Conversation, column: int):
    if column == 0:
        return conv.display_title.lower()
    if column == 1:
        return conv.provider
    if column == 2:
        return conv.updated_at or ""
    if column == 3:
        return conv.user_message_count
    if column == 4:
        return conv.tool_call_count
    if column == 5:
        return _total_tokens(conv)
    return conv.cost_usd or 0.0


def _total_tokens(conv: Conversation) -> int:
    return conv.input_tokens + conv.output_tokens + conv.cache_read_tokens + conv.cache_write_tokens


class ProjectsPage(wx.Panel):
    """Projects list | conversations tree | conversation view."""

    def __init__(self, parent: wx.Window, index_repository: IndexRepository,
                 settings_repository: SettingsRepository, on_rescan: Callable[[], None],
                 on_status: Callable[[str], None]) -> None:
        super().__init__(parent)
        self._index = index_repository
        self._settings = settings_repository
        self._on_rescan = on_rescan
        self._on_status = on_status
        self._groups: List[ProjectGroup] = []
        self._conversations: Dict[int, Conversation] = {}
        self._current_group: Optional[ProjectGroup] = None

        self._outer = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        self._outer.SetMinimumPaneSize(160)
        left = self._build_projects_pane(self._outer)
        self._inner = wx.SplitterWindow(self._outer, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        self._inner.SetMinimumPaneSize(200)
        middle = self._build_conversations_pane(self._inner)
        self.view = ConversationView(self._inner, index_repository, settings_repository)

        self._outer.SplitVertically(left, self._inner, self._settings.get_int(PROJECT_SASH_SETTING_KEY) or 290)
        self._inner.SplitVertically(middle, self.view,
                                    self._settings.get_int(CONVERSATION_SASH_SETTING_KEY) or 440)
        self._outer.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self._on_sash_changed)
        self._inner.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self._on_sash_changed)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._outer, 1, wx.EXPAND)
        self.SetSizer(sizer)

    # ------------------------------------------------------------------ build
    def _build_projects_pane(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent)
        self._provider_choice = wx.Choice(panel, choices=["All tools"] + list(PROVIDER_LABELS.values()))
        stored = self._settings.get(PROVIDER_FILTER_SETTING_KEY)
        ids = list(PROVIDER_LABELS)
        self._provider_choice.SetSelection(ids.index(stored) + 1 if stored in ids else 0)
        self._filter = wx.SearchCtrl(panel)
        self._filter.SetDescriptiveText("Filter projects")
        self._filter.ShowCancelButton(True)
        rescan = wx.Button(panel, label="Rescan")
        rescan.SetToolTip("Re-read Claude Code / OpenCode data now (F5)")

        self._projects = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self._projects.InsertColumn(0, "Project", width=150)
        self._projects.InsertColumn(1, "Conv.", wx.LIST_FORMAT_RIGHT, width=50)
        self._projects.InsertColumn(2, "Last activity", width=125)
        self._projects.InsertColumn(3, "Tools", width=140)

        top = wx.BoxSizer(wx.HORIZONTAL)
        top.Add(self._provider_choice, 1, wx.RIGHT, 4)
        top.Add(rescan, 0)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(top, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(self._filter, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        sizer.Add(self._projects, 1, wx.EXPAND)
        panel.SetSizer(sizer)

        self._provider_choice.Bind(wx.EVT_CHOICE, self._on_provider_filter)
        self._filter.Bind(wx.EVT_TEXT, lambda evt: self.reload_projects())
        self._filter.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, lambda evt: self._filter.SetValue(""))
        rescan.Bind(wx.EVT_BUTTON, lambda evt: self._on_rescan())
        self._projects.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_project_selected)
        return panel

    def _build_conversations_pane(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent)
        self._project_label = wx.StaticText(panel, label="Select a project")
        font = self._project_label.GetFont()
        font.MakeBold()
        self._project_label.SetFont(font)
        self._project_path = wx.StaticText(panel, label="", style=wx.ST_ELLIPSIZE_MIDDLE)
        self._project_path.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        self._tree = dv.TreeListCtrl(panel, style=dv.TL_SINGLE)
        for label, width, numeric in CONVERSATION_COLUMNS:
            align = wx.ALIGN_RIGHT if numeric else wx.ALIGN_LEFT
            self._tree.AppendColumn(label, width, align, wx.COL_RESIZABLE | wx.COL_SORTABLE)
        self._comparator = _ConversationComparator(self)
        self._tree.SetItemComparator(self._comparator)
        self._item_ids: Dict[int, dv.TreeListItem] = {}

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._project_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        sizer.Add(self._project_path, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        sizer.Add(self._tree, 1, wx.EXPAND)
        panel.SetSizer(sizer)

        self._tree.Bind(dv.EVT_TREELIST_SELECTION_CHANGED, self._on_conversation_selected)
        return panel

    # ----------------------------------------------------------------- public
    def restore_last_selection(self) -> None:
        """Startup: reselect the project + conversation open last time."""
        self.reload_projects()
        last_project = self._settings.get_int(LAST_PROJECT_SETTING_KEY)
        if last_project is not None:
            self._select_group_containing(last_project)
        last_conv = self._settings.get_int(LAST_CONVERSATION_SETTING_KEY)
        if last_conv is not None and last_conv in self._conversations:
            self._select_conversation(last_conv)

    def refresh_after_sync(self) -> None:
        """Reload lists keeping the current project/conversation selected;
        re-render the open conversation only if it actually changed."""
        current_conv_id = self.view.current_conversation_id()
        before = self._conversations.get(current_conv_id) if current_conv_id else None
        project_id = self._current_group.project_ids[0] if self._current_group else None
        self.reload_projects()
        if project_id is not None:
            self._select_group_containing(project_id, show=False)
            self._reload_conversations()
        if current_conv_id is not None and current_conv_id in self._conversations:
            self._select_item(current_conv_id)
            after = self._conversations[current_conv_id]
            if before is None or (after.updated_at, after.message_count) != (before.updated_at, before.message_count):
                self.view.show_conversation(current_conv_id, self._current_group.path if self._current_group else "",
                                            keep_scroll=True)

    def reload_projects(self) -> None:
        selected_path = self._current_group.path if self._current_group else None
        sel = self._provider_choice.GetSelection()
        provider = list(PROVIDER_LABELS)[sel - 1] if sel > 0 else None
        self._groups = self._index.list_project_groups(provider, self._filter.GetValue().strip())
        self._projects.Freeze()
        try:
            self._projects.DeleteAllItems()
            for i, g in enumerate(self._groups):
                self._projects.InsertItem(i, g.name)
                self._projects.SetItem(i, 1, str(g.conversation_count))
                self._projects.SetItem(i, 2, format_datetime(g.last_activity))
                self._projects.SetItem(i, 3, ", ".join(PROVIDER_LABELS.get(p, p) for p in g.providers))
                if g.path == selected_path:
                    self._projects.SetItemState(i, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
        finally:
            self._projects.Thaw()
        self._on_status(f"{len(self._groups)} projects")

    # ---------------------------------------------------------------- helpers
    def _select_group_containing(self, project_id: int, show: bool = True) -> None:
        for i, g in enumerate(self._groups):
            if project_id in g.project_ids:
                if show:
                    # May or may not fire EVT_LIST_ITEM_SELECTED depending
                    # on the platform - load explicitly either way.
                    self._projects.SetItemState(i, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
                    self._projects.EnsureVisible(i)
                    if self._current_group is None or self._current_group.path != g.path:
                        self._current_group = g
                        self._reload_conversations()
                else:
                    self._current_group = g
                return

    def _conversation_for_item(self, item: dv.TreeListItem) -> Optional[Conversation]:
        data = self._tree.GetItemData(item)
        return self._conversations.get(data) if data is not None else None

    def _reload_conversations(self) -> None:
        g = self._current_group
        self._tree.DeleteAllItems()
        self._item_ids = {}
        self._conversations = {}
        if g is None:
            self._project_label.SetLabel("Select a project")
            self._project_path.SetLabel("")
            return
        self._project_label.SetLabel(g.name)
        self._project_path.SetLabel(g.path)
        self._project_path.SetToolTip(g.path)
        convs = self._index.list_conversations(g.project_ids)
        self._conversations = {c.id: c for c in convs}
        by_external = {(c.provider, c.external_id): c for c in convs}
        root = self._tree.GetRootItem()
        # Parents first so subagent threads can be appended under them.
        for c in sorted(convs, key=lambda c: c.parent_external_id is not None):
            parent_conv = by_external.get((c.provider, c.parent_external_id)) if c.parent_external_id else None
            parent_item = self._item_ids.get(parent_conv.id) if parent_conv else None
            title = c.display_title
            if c.parent_external_id:
                title = f"↳ {title}"
            if c.missing_from_source:
                title = f"{title}  (archived)"
            item = self._tree.AppendItem(parent_item or root, title)
            self._tree.SetItemData(item, c.id)
            self._tree.SetItemText(item, 1, PROVIDER_LABELS.get(c.provider, c.provider))
            self._tree.SetItemText(item, 2, format_datetime(c.updated_at or c.started_at))
            self._tree.SetItemText(item, 3, str(c.user_message_count))
            self._tree.SetItemText(item, 4, str(c.tool_call_count))
            self._tree.SetItemText(item, 5, format_tokens(_total_tokens(c)))
            self._tree.SetItemText(item, 6, format_cost(c.cost_usd))
            self._item_ids[c.id] = item

    def _select_item(self, conversation_id: int) -> None:
        item = self._item_ids.get(conversation_id)
        if item is None:
            return
        parent = self._tree.GetItemParent(item)
        if parent.IsOk() and parent != self._tree.GetRootItem():
            self._tree.Expand(parent)
        self._tree.Select(item)
        self._tree.EnsureVisible(item)

    def _select_conversation(self, conversation_id: int) -> None:
        self._select_item(conversation_id)
        self._show(conversation_id)

    def _show(self, conversation_id: int) -> None:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return
        self._settings.set_int(LAST_CONVERSATION_SETTING_KEY, conversation_id)
        self.view.show_conversation(conversation_id, self._current_group.path if self._current_group else "")
        self._on_status(f"{conv.display_title[:80]}")

    # ----------------------------------------------------------------- events
    def _on_provider_filter(self, event: wx.CommandEvent) -> None:
        sel = self._provider_choice.GetSelection()
        self._settings.set(PROVIDER_FILTER_SETTING_KEY, list(PROVIDER_LABELS)[sel - 1] if sel > 0 else None)
        self.reload_projects()

    def _on_project_selected(self, event: wx.ListEvent) -> None:
        index = event.GetIndex()
        if not (0 <= index < len(self._groups)):
            return
        group = self._groups[index]
        if self._current_group is not None and self._current_group.path == group.path and self._conversations:
            return
        self._current_group = group
        self._settings.set_int(LAST_PROJECT_SETTING_KEY, group.project_ids[0])
        self._reload_conversations()
        self.view.clear()

    def _on_conversation_selected(self, event: dv.TreeListEvent) -> None:
        item = self._tree.GetSelection()
        if not item.IsOk():
            return
        conv_id = self._tree.GetItemData(item)
        if conv_id is not None and conv_id != self.view.current_conversation_id():
            self._show(conv_id)

    def _on_sash_changed(self, event: wx.SplitterEvent) -> None:
        self._settings.set_int(PROJECT_SASH_SETTING_KEY, self._outer.GetSashPosition())
        self._settings.set_int(CONVERSATION_SASH_SETTING_KEY, self._inner.GetSashPosition())
        event.Skip()
