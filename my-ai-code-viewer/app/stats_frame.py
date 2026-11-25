"""File > Stats: usage statistics over the archive in a separate window.

Filters (range, agent) on top, KPI tiles, then one notebook tab per topic
(Agents, Projects, Conversations, Activity, Tools), each a chart over a
sortable table. Double-clicking a project or conversation (in a chart or a
table) reveals it in the main window. Data comes from the wx-free
`app/stats.py`; charts from `app/stats_charts.py`.
"""
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

import wx

from app import stats_charts, theme
from app.formatting import format_cost, format_datetime, format_tokens
from app.providers import PROVIDER_LABELS
from app.repositories import SettingsRepository
from app.stats import RANGES, ConversationStats, Stats, Totals, load_stats, since_for
from app.stats_charts import BarRow, ColumnChart, Heatmap, HBarChart

STATS_RANGE_SETTING_KEY = "stats_range"
STATS_PROVIDER_SETTING_KEY = "stats_provider"
STATS_TAB_SETTING_KEY = "stats_tab"
DEFAULT_RANGE_INDEX = 1  # last 30 days

PROVIDER_ORDER = {p: i for i, p in enumerate(PROVIDER_LABELS)}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _pct(value: Optional[float]) -> str:
    return "" if value is None else f"{value * 100:.0f}%"


def _cost(t: Totals) -> str:
    text = format_cost(t.cost_usd) if t.conversations > t.cost_unknown else "n/a"
    return text + ("*" if t.cost_unknown and t.conversations > t.cost_unknown else "")


def _duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def _agents(providers: Sequence[str]) -> str:
    return ", ".join(PROVIDER_LABELS.get(p, p) for p in providers)


@dataclass
class Metric:
    label: str
    value: Callable[[Any], float]
    text: Callable[[Any], str]


TOTALS_METRICS = [
    Metric("Conversations", lambda t: t.conversations, lambda t: f"{t.conversations:,}"),
    Metric("Prompts (Q&A)", lambda t: t.prompts, lambda t: f"{t.prompts:,}"),
    Metric("Cost", lambda t: t.cost_usd, _cost),
    Metric("Tokens", lambda t: t.tokens, lambda t: format_tokens(t.tokens)),
    Metric("Output tokens", lambda t: t.output_tokens, lambda t: format_tokens(t.output_tokens)),
    Metric("Tool calls", lambda t: t.tool_calls, lambda t: f"{t.tool_calls:,}"),
    Metric("Thinking blocks", lambda t: t.thinking, lambda t: f"{t.thinking:,}"),
]


@dataclass
class Column:
    title: str
    width: int
    text: Callable[[Any], str]
    key: Callable[[Any], Any]
    numeric: bool = True


class StatsTable(wx.ListCtrl):
    """Report list with click-to-sort headers (arrow in the label) and a
    double-click callback receiving the row object."""

    def __init__(self, parent: wx.Window, columns: List[Column], sort_column: int = 1,
                 on_activate: Optional[Callable[[Any], None]] = None):
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.columns = columns
        self.rows: List[Any] = []
        self.sort_column = sort_column
        self.sort_ascending = not columns[sort_column].numeric
        self.on_activate = on_activate
        for i, c in enumerate(columns):
            self.InsertColumn(i, c.title, wx.LIST_FORMAT_RIGHT if c.numeric else wx.LIST_FORMAT_LEFT, c.width)
        self.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_activated)

    def set_rows(self, rows: Sequence[Any]) -> None:
        self.rows = list(rows)
        self._fill()

    def _fill(self) -> None:
        col = self.columns[self.sort_column]

        def key(row):
            k = col.key(row)
            return (k is None, k if k is not None else 0)

        self.rows.sort(key=key, reverse=not self.sort_ascending)
        if not self.sort_ascending:  # keep "no value" rows last either way
            self.rows.sort(key=lambda r: col.key(r) is None)
        self.Freeze()
        try:
            self.DeleteAllItems()
            for i, row in enumerate(self.rows):
                self.InsertItem(i, self.columns[0].text(row))
                for j, c in enumerate(self.columns[1:], 1):
                    self.SetItem(i, j, c.text(row))
            for j, c in enumerate(self.columns):
                arrow = (" ▲" if self.sort_ascending else " ▼") if j == self.sort_column else ""
                info = self.GetColumn(j)
                info.SetText(c.title + arrow)
                self.SetColumn(j, info)
        finally:
            self.Thaw()

    def _on_col_click(self, event: wx.ListEvent) -> None:
        j = event.GetColumn()
        if j < 0:
            return
        if j == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = j
            self.sort_ascending = not self.columns[j].numeric
        self._fill()

    def _on_activated(self, event: wx.ListEvent) -> None:
        i = event.GetIndex()
        if self.on_activate and 0 <= i < len(self.rows):
            self.on_activate(self.rows[i])


class KpiTile(wx.Panel):
    def __init__(self, parent: wx.Window, caption: str):
        super().__init__(parent)
        self._caption = wx.StaticText(self, label=caption)
        self._caption.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        self._value = wx.StaticText(self, label="-")
        font = self._value.GetFont()
        font.SetFractionalPointSize(font.GetFractionalPointSize() * 1.7)
        font.MakeBold()
        self._value.SetFont(font)
        self._sub = wx.StaticText(self, label="")
        self._sub.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._caption, 0, wx.BOTTOM, 2)
        sizer.Add(self._value, 0)
        sizer.Add(self._sub, 0, wx.TOP, 2)
        self.SetSizer(sizer)

    def set(self, value: str, sub: str = "", tooltip: str = "") -> None:
        self._value.SetLabel(value)
        self._sub.SetLabel(sub)
        for w in (self, self._caption, self._value, self._sub):
            w.SetToolTip(tooltip or None)


def _section_title(parent: wx.Window, text: str) -> wx.StaticText:
    label = wx.StaticText(parent, label=text)
    font = label.GetFont()
    font.MakeBold()
    label.SetFont(font)
    return label


class StatsFrame(wx.Frame):
    def __init__(self, parent: wx.Window, conn, settings: SettingsRepository,
                 on_open_project: Callable[[int], None],
                 on_open_conversation: Callable[[int, int], None]) -> None:
        super().__init__(parent, title="Stats - My AI Code Viewer", size=(1250, 860))
        self.SetMinSize((800, 560))
        self._conn = conn
        self._settings = settings
        self._on_open_project = on_open_project
        self._on_open_conversation = on_open_conversation
        self._stats: Optional[Stats] = None
        self._dark = self._want_dark()
        self._charts: List[stats_charts.Chart] = []

        root = wx.Panel(self)
        self._root = root
        self._range = wx.Choice(root, choices=[label for label, _ in RANGES])
        stored = settings.get_int(STATS_RANGE_SETTING_KEY)
        self._range.SetSelection(stored if stored is not None and 0 <= stored < len(RANGES) else DEFAULT_RANGE_INDEX)
        self._provider = wx.Choice(root, choices=["All agents"] + list(PROVIDER_LABELS.values()))
        ids = list(PROVIDER_LABELS)
        stored_p = settings.get(STATS_PROVIDER_SETTING_KEY)
        self._provider.SetSelection(ids.index(stored_p) + 1 if stored_p in ids else 0)
        note = wx.StaticText(root, label="Totals cover conversations active in the range.  "
                                          "* = some conversations have no cost data.")
        note.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        filters = wx.BoxSizer(wx.HORIZONTAL)
        filters.Add(wx.StaticText(root, label="Range:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        filters.Add(self._range, 0, wx.RIGHT, 12)
        filters.Add(wx.StaticText(root, label="Agent:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        filters.Add(self._provider, 0, wx.RIGHT, 12)
        filters.AddStretchSpacer()
        filters.Add(note, 0, wx.ALIGN_CENTER_VERTICAL)

        self._tiles = {key: KpiTile(root, caption) for key, caption in (
            ("conversations", "Conversations"), ("prompts", "Prompts (Q&A)"), ("cost", "Cost"),
            ("tokens", "Tokens"), ("tools", "Tool calls"), ("days", "Active days"),
        )}
        tiles = wx.BoxSizer(wx.HORIZONTAL)
        for tile in self._tiles.values():
            tiles.Add(tile, 1, wx.RIGHT, 16)

        self._notebook = wx.Notebook(root)
        self._build_agents_tab()
        self._build_projects_tab()
        self._build_conversations_tab()
        self._build_activity_tab()
        self._build_tools_tab()
        tab = settings.get_int(STATS_TAB_SETTING_KEY)
        if tab is not None and 0 <= tab < self._notebook.GetPageCount():
            self._notebook.SetSelection(tab)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(filters, 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(tiles, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        sizer.Add(self._notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        root.SetSizer(sizer)

        self._range.Bind(wx.EVT_CHOICE, self._on_filter)
        self._provider.Bind(wx.EVT_CHOICE, self._on_filter)
        self._notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_tab)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.CentreOnParent()
        self.reload()

    # ------------------------------------------------------------------ build
    def _chart(self, chart: stats_charts.Chart) -> stats_charts.Chart:
        chart.set_theme(self._chart_theme())
        self._charts.append(chart)
        return chart

    def _metric_choice(self, parent: wx.Window, on_change: Callable[[], None]) -> wx.Choice:
        choice = wx.Choice(parent, choices=[m.label for m in TOTALS_METRICS])
        choice.SetSelection(0)
        choice.Bind(wx.EVT_CHOICE, lambda evt: on_change())
        return choice

    def _totals_columns(self, first: Column) -> List[Column]:
        return [
            first,
            Column("Conv.", 60, lambda t: f"{t.conversations:,}", lambda t: t.conversations),
            Column("Prompts", 70, lambda t: f"{t.prompts:,}", lambda t: t.prompts),
            Column("Prompts/conv.", 95, lambda t: f"{t.prompts / t.conversations:.1f}" if t.conversations else "",
                   lambda t: t.prompts / t.conversations if t.conversations else None),
            Column("Thinking", 70, lambda t: f"{t.thinking:,}", lambda t: t.thinking),
            Column("Tool calls", 80, lambda t: f"{t.tool_calls:,}", lambda t: t.tool_calls),
            Column("Tool errors", 85, lambda t: f"{t.tool_errors:,}", lambda t: t.tool_errors),
            Column("Interrupts", 80, lambda t: f"{t.interrupts:,}", lambda t: t.interrupts),
            Column("Tokens", 75, lambda t: format_tokens(t.tokens), lambda t: t.tokens),
            Column("Output", 70, lambda t: format_tokens(t.output_tokens), lambda t: t.output_tokens),
            Column("Cache hit", 75, lambda t: _pct(t.cache_hit_ratio), lambda t: t.cache_hit_ratio),
            Column("Cost", 80, _cost, lambda t: t.cost_usd if t.conversations > t.cost_unknown else None),
            Column("Last activity", 130, lambda t: format_datetime(t.last_activity),
                   lambda t: t.last_activity or "", numeric=False),
        ]

    def _build_agents_tab(self) -> None:
        page = wx.Panel(self._notebook)
        self._agent_metric = self._metric_choice(page, self._fill_agents)
        self._agent_bars = self._chart(HBarChart(page))
        self._model_bars = self._chart(HBarChart(page, max_rows=8))
        self._agent_table = StatsTable(page, self._totals_columns(
            Column("Agent", 130, lambda t: t.label, lambda t: t.label, numeric=False)))

        left = wx.BoxSizer(wx.VERTICAL)
        head = wx.BoxSizer(wx.HORIZONTAL)
        head.Add(_section_title(page, "Usage by agent"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        head.AddStretchSpacer()
        head.Add(self._agent_metric, 0)
        left.Add(head, 0, wx.EXPAND | wx.BOTTOM, 4)
        left.Add(self._agent_bars, 1, wx.EXPAND)
        right = wx.BoxSizer(wx.VERTICAL)
        right.Add(_section_title(page, "Conversations by model"), 0, wx.BOTTOM | wx.TOP, 6)
        right.Add(self._model_bars, 1, wx.EXPAND)
        charts = wx.BoxSizer(wx.HORIZONTAL)
        charts.Add(left, 1, wx.EXPAND | wx.RIGHT, 12)
        charts.Add(right, 1, wx.EXPAND)
        self._finish_page(page, "Agents", charts, self._agent_table)

    def _build_projects_tab(self) -> None:
        page = wx.Panel(self._notebook)
        self._project_metric = self._metric_choice(page, self._fill_projects)
        self._project_bars = self._chart(HBarChart(page, max_rows=12))
        self._project_bars.on_activate = lambda i: self._open_project(self._top_projects[i])
        self._project_table = StatsTable(
            page,
            self._totals_columns(Column("Project", 170, lambda t: t.label, lambda t: t.label.lower(), numeric=False))
            + [Column("Agents", 150, lambda t: _agents(t.providers), lambda t: _agents(t.providers), numeric=False),
               Column("Path", 300, lambda t: t.key, lambda t: t.key, numeric=False)],
            on_activate=self._open_project)
        self._top_projects: List[Totals] = []

        box = wx.BoxSizer(wx.VERTICAL)
        head = wx.BoxSizer(wx.HORIZONTAL)
        head.Add(_section_title(page, "Top projects"), 0, wx.ALIGN_CENTER_VERTICAL)
        head.AddStretchSpacer()
        head.Add(self._project_metric, 0)
        box.Add(head, 0, wx.EXPAND | wx.BOTTOM, 4)
        box.Add(self._project_bars, 1, wx.EXPAND)
        self._finish_page(page, "Projects", box, self._project_table,
                          hint="Double-click a project to open it in the main window.")

    def _build_conversations_tab(self) -> None:
        page = wx.Panel(self._notebook)
        self._qa_hist = self._chart(ColumnChart(page, value_name="conversations"))
        self._long_bars = self._chart(HBarChart(page, max_rows=10))
        self._long_bars.on_activate = lambda i: self._open_conversation(self._longest[i])
        self._longest: List[ConversationStats] = []
        self._conv_table = StatsTable(page, [
            Column("Title", 280, lambda c: c.title + ("  (archived)" if c.archived else ""),
                   lambda c: c.title.lower(), numeric=False),
            Column("Project", 140, lambda c: c.project_name, lambda c: c.project_name.lower(), numeric=False),
            Column("Agent", 95, lambda c: PROVIDER_LABELS.get(c.provider, c.provider), lambda c: c.provider,
                   numeric=False),
            Column("Started", 130, lambda c: format_datetime(c.started_at), lambda c: c.started_at or "",
                   numeric=False),
            Column("Duration", 75, lambda c: _duration(c.duration_seconds), lambda c: c.duration_seconds),
            Column("Prompts", 65, lambda c: str(c.prompts), lambda c: c.prompts),
            Column("Thinking", 70, lambda c: str(c.thinking), lambda c: c.thinking),
            Column("Tool calls", 75, lambda c: str(c.tool_calls), lambda c: c.tool_calls),
            Column("Errors", 60, lambda c: str(c.tool_errors), lambda c: c.tool_errors),
            Column("Interrupts", 75, lambda c: str(c.interrupts), lambda c: c.interrupts),
            Column("Subagents", 75, lambda c: str(c.subagents), lambda c: c.subagents),
            Column("Tokens", 70, lambda c: format_tokens(c.tokens), lambda c: c.tokens),
            Column("Cost", 70, lambda c: format_cost(c.cost_usd), lambda c: c.cost_usd),
            Column("Model", 160, lambda c: c.model or "", lambda c: c.model or "", numeric=False),
        ], sort_column=3, on_activate=self._open_conversation)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(_section_title(page, "Conversation length (prompts per conversation)"), 0, wx.BOTTOM | wx.TOP, 6)
        left.Add(self._qa_hist, 1, wx.EXPAND)
        right = wx.BoxSizer(wx.VERTICAL)
        right.Add(_section_title(page, "Longest conversations (prompts)"), 0, wx.BOTTOM | wx.TOP, 6)
        right.Add(self._long_bars, 1, wx.EXPAND)
        charts = wx.BoxSizer(wx.HORIZONTAL)
        charts.Add(left, 1, wx.EXPAND | wx.RIGHT, 12)
        charts.Add(right, 1, wx.EXPAND)
        self._finish_page(page, "Conversations", charts, self._conv_table,
                          hint="Double-click a conversation to open it in the main window.")

    def _build_activity_tab(self) -> None:
        page = wx.Panel(self._notebook)
        self._daily = self._chart(ColumnChart(page, value_name="prompts"))
        self._heatmap = self._chart(Heatmap(page))
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(_section_title(page, "Prompts per day"), 0, wx.BOTTOM | wx.TOP, 6)
        box.Add(self._daily, 1, wx.EXPAND | wx.BOTTOM, 12)
        box.Add(_section_title(page, "When you prompt (local time, weekday x hour)"), 0, wx.BOTTOM, 6)
        box.Add(self._heatmap, 1, wx.EXPAND)
        page.SetSizer(wx.BoxSizer(wx.VERTICAL))
        page.GetSizer().Add(box, 1, wx.EXPAND | wx.ALL, 10)
        self._notebook.AddPage(page, "Activity")

    def _build_tools_tab(self) -> None:
        page = wx.Panel(self._notebook)
        self._tool_bars = self._chart(HBarChart(page, max_rows=15))
        columns = [
            Column("Tool", 180, lambda t: t.name, lambda t: t.name.lower(), numeric=False),
            Column("Calls", 70, lambda t: f"{t.calls:,}", lambda t: t.calls),
            Column("Conversations", 100, lambda t: f"{t.conversations:,}", lambda t: t.conversations),
            Column("Errors", 65, lambda t: f"{t.errors:,}", lambda t: t.errors),
            Column("Error rate", 80, lambda t: _pct(t.error_rate), lambda t: t.error_rate),
        ] + [Column(label, 100, lambda t, p=p: f"{t.by_provider.get(p, 0):,}", lambda t, p=p: t.by_provider.get(p, 0))
             for p, label in PROVIDER_LABELS.items()]
        self._tool_table = StatsTable(page, columns)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(_section_title(page, "Most used tools (calls)"), 0, wx.BOTTOM | wx.TOP, 6)
        box.Add(self._tool_bars, 1, wx.EXPAND)
        self._finish_page(page, "Tools", box, self._tool_table)

    def _finish_page(self, page: wx.Panel, title: str, charts: wx.Sizer, table: StatsTable,
                     hint: str = "") -> None:
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(charts, 3, wx.EXPAND | wx.ALL, 10)
        if hint:
            label = wx.StaticText(page, label=hint)
            label.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            sizer.Add(label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        sizer.Add(table, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        page.SetSizer(sizer)
        self._notebook.AddPage(page, title)

    # ------------------------------------------------------------------ data
    def reload(self) -> None:
        """Recompute everything (filters changed, or a sync brought new data)."""
        days = RANGES[self._range.GetSelection()][1]
        sel = self._provider.GetSelection()
        provider = list(PROVIDER_LABELS)[sel - 1] if sel > 0 else None
        self._stats = load_stats(self._conn, since_for(days), provider, PROVIDER_LABELS)
        self._fill_tiles()
        self._fill_agents()
        self._fill_projects()
        self._fill_conversations()
        self._fill_activity()
        self._fill_tools()
        self._root.Layout()

    def _fill_tiles(self) -> None:
        s = self._stats
        t = s.total
        self._tiles["conversations"].set(f"{t.conversations:,}", f"{len(s.projects)} projects")
        avg = f"{t.prompts / t.conversations:.1f} per conversation" if t.conversations else ""
        self._tiles["prompts"].set(f"{t.prompts:,}", avg)
        known = t.conversations - t.cost_unknown
        self._tiles["cost"].set(
            format_cost(t.cost_usd) if known else "n/a",
            f"{t.cost_unknown} without cost data" if t.cost_unknown else
            (f"{format_cost(t.cost_usd / t.prompts)} per prompt" if t.prompts else ""),
            "Claude Code records cost only in sessions with a cost-state line." if t.cost_unknown else "")
        self._tiles["tokens"].set(format_tokens(t.tokens), f"cache hit {_pct(t.cache_hit_ratio)}" if t.tokens else "",
                                  f"in {format_tokens(t.input_tokens)}, out {format_tokens(t.output_tokens)}, "
                                  f"cache read {format_tokens(t.cache_read_tokens)}, "
                                  f"cache write {format_tokens(t.cache_write_tokens)}")
        self._tiles["tools"].set(f"{t.tool_calls:,}", f"{t.tool_errors:,} errors" if t.tool_calls else "")
        self._tiles["days"].set(str(s.active_days), f"of {len(s.days)} days" if s.days else "")

    def _bars(self, items: Sequence[Totals], metric: Metric, colour=None) -> List[BarRow]:
        rows = sorted(items, key=lambda t: metric.value(t), reverse=True)
        return [BarRow(t.label, metric.value(t), metric.text(t), colour_index=colour(t) if colour else 0)
                for t in rows]

    def _fill_agents(self) -> None:
        s = self._stats
        metric = TOTALS_METRICS[self._agent_metric.GetSelection()]
        self._agent_bars.set_rows(self._bars(
            s.agents, metric, lambda t: stats_charts.series_colour_index(t.key, PROVIDER_ORDER)))
        self._model_bars.set_rows([BarRow(m, n, f"{n:,}") for m, n in s.models])
        self._agent_table.set_rows(s.agents)

    def _fill_projects(self) -> None:
        metric = TOTALS_METRICS[self._project_metric.GetSelection()]
        ranked = sorted(self._stats.projects, key=lambda t: metric.value(t), reverse=True)
        self._top_projects = ranked[: self._project_bars.max_rows]
        self._project_bars.set_rows([BarRow(t.label, metric.value(t), metric.text(t), f"{t.key}\n{metric.label}: "
                                            f"{metric.text(t)}\nDouble-click to open") for t in self._top_projects])
        self._project_table.set_rows(self._stats.projects)

    def _fill_conversations(self) -> None:
        s = self._stats
        self._qa_hist.set_data([label for label, _ in s.qa_histogram],
                               [("Conversations", [n for _, n in s.qa_histogram], 0)])
        self._longest = sorted((c for c in s.conversations if c.prompts), key=lambda c: -c.prompts)[:10]
        self._long_bars.set_rows([
            BarRow(c.title, c.prompts, str(c.prompts),
                   f"{c.title}\n{c.project_name} - {PROVIDER_LABELS.get(c.provider, c.provider)}\n"
                   f"{c.prompts} prompts, {c.tool_calls} tool calls\nDouble-click to open",
                   colour_index=stats_charts.series_colour_index(c.provider, PROVIDER_ORDER))
            for c in self._longest])
        self._conv_table.set_rows(s.conversations)

    def _fill_activity(self) -> None:
        s = self._stats
        categories = [d.strftime("%a %Y-%m-%d") for d in s.days]
        ticks = [d.strftime("%m-%d") for d in s.days]
        self._daily.set_data(categories, [
            (PROVIDER_LABELS.get(p, p), values, stats_charts.series_colour_index(p, PROVIDER_ORDER))
            for p, values in sorted(s.daily.items(), key=lambda kv: PROVIDER_ORDER.get(kv[0], 99))], ticks)
        self._heatmap.set_data(s.heatmap, WEEKDAYS, [f"{h:02d}" for h in range(24)])

    def _fill_tools(self) -> None:
        tools = self._stats.tools
        self._tool_bars.set_rows([
            BarRow(t.name, t.calls, f"{t.calls:,}",
                   f"{t.name}: {t.calls:,} calls in {t.conversations} conversations, "
                   f"{t.errors} errors ({_pct(t.error_rate)})") for t in tools])
        self._tool_table.set_rows(tools)

    # ----------------------------------------------------------------- theme
    def _want_dark(self) -> bool:
        value = theme.normalize(self._settings.get(theme.THEME_SETTING_KEY))
        return theme.resolve_dark(value, theme.system_is_dark() if value == theme.SYSTEM else False)

    def _chart_theme(self) -> stats_charts.ChartTheme:
        return stats_charts.DARK if self._dark else stats_charts.LIGHT

    def refresh_theme(self) -> None:
        dark = self._want_dark()
        if dark != self._dark:
            self._dark = dark
            for chart in self._charts:
                chart.set_theme(self._chart_theme())

    # ---------------------------------------------------------------- events
    def _open_project(self, t: Totals) -> None:
        if t.project_ids:
            self._on_open_project(t.project_ids[0])

    def _open_conversation(self, c: ConversationStats) -> None:
        self._on_open_conversation(c.project_id, c.id)

    def _on_filter(self, event: wx.CommandEvent) -> None:
        self._settings.set_int(STATS_RANGE_SETTING_KEY, self._range.GetSelection())
        sel = self._provider.GetSelection()
        self._settings.set(STATS_PROVIDER_SETTING_KEY, list(PROVIDER_LABELS)[sel - 1] if sel > 0 else None)
        self.reload()

    def _on_tab(self, event: wx.BookCtrlEvent) -> None:
        event.Skip()
        self._settings.set_int(STATS_TAB_SETTING_KEY, event.GetSelection())

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            event.Skip()
