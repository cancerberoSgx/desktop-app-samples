"""Usage statistics over the archive (pure SQL + Python, wx-free) - backs
the Stats window (stats_frame.py).

Units:
- A *conversation* is a top-level session. Subagent threads are rolled
  into their parent (their tokens, tool calls, thinking count toward it)
  but never counted as conversations or prompts of their own.
- A *prompt* (= one Q&A) is a turn start as defined by
  conversation_turns.split_turns: a user text block that isn't a
  "[Request interrupted" marker and doesn't directly follow another prompt
  block. Computed in SQL with LAG() so the stats never load message text.
- The range filter keeps conversations *active* in the range (updated_at
  >= since); their totals are whole-conversation totals (tokens/cost aren't
  stored per message). Activity charts use prompt timestamps, which are
  exact.
- Cost: Claude Code only records it when a `cost-state` line exists, so
  some conversations have none; they're counted in `cost_unknown`.
"""
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.formatting import parse_iso

RANGES: Tuple[Tuple[str, Optional[int]], ...] = (
    ("Last 7 days", 7), ("Last 30 days", 30), ("Last 90 days", 90), ("Last year", 365), ("All time", None),
)

# Q&A-per-conversation histogram buckets: (label, lowest count).
QA_BUCKETS: Tuple[Tuple[str, int], ...] = (
    ("1", 1), ("2-3", 2), ("4-7", 4), ("8-15", 8), ("16-31", 16), ("32+", 32),
)


@dataclass
class ConversationStats:
    id: int
    project_id: int
    project_path: str
    project_name: str
    provider: str
    title: str
    started_at: Optional[str]
    updated_at: Optional[str]
    model: Optional[str]
    prompts: int = 0
    thinking: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    interrupts: int = 0
    subagents: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Optional[float] = None
    archived: bool = False

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def duration_seconds(self) -> Optional[int]:
        a, b = parse_iso(self.started_at), parse_iso(self.updated_at)
        return int((b - a).total_seconds()) if a and b else None


@dataclass
class Totals:
    """A row of summed metrics - one per agent, per project, or overall."""
    key: str
    label: str
    conversations: int = 0
    prompts: int = 0
    thinking: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    interrupts: int = 0
    subagents: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    cost_unknown: int = 0
    last_activity: Optional[str] = None
    providers: List[str] = field(default_factory=list)
    project_ids: List[int] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def cache_hit_ratio(self) -> Optional[float]:
        """Share of prompt tokens served from the cache."""
        prompt_side = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return self.cache_read_tokens / prompt_side if prompt_side else None

    def add(self, c: ConversationStats) -> None:
        self.conversations += 1
        for name in ("prompts", "thinking", "tool_calls", "tool_errors", "interrupts", "subagents",
                     "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            setattr(self, name, getattr(self, name) + getattr(c, name))
        if c.cost_usd is None:
            self.cost_unknown += 1
        else:
            self.cost_usd += c.cost_usd
        if (c.updated_at or "") > (self.last_activity or ""):
            self.last_activity = c.updated_at
        if c.provider not in self.providers:
            self.providers.append(c.provider)
        if c.project_id not in self.project_ids:
            self.project_ids.append(c.project_id)


@dataclass
class ToolStats:
    name: str
    calls: int = 0
    errors: int = 0
    conversations: int = 0
    by_provider: Dict[str, int] = field(default_factory=dict)

    @property
    def error_rate(self) -> Optional[float]:
        return self.errors / self.calls if self.calls else None


@dataclass
class Stats:
    since: Optional[str]
    total: Totals
    agents: List[Totals]                  # by provider, most conversations first
    projects: List[Totals]                # by project path (providers merged)
    conversations: List[ConversationStats]
    tools: List[ToolStats]                # most calls first
    qa_histogram: List[Tuple[str, int]]   # (bucket label, conversations)
    heatmap: List[List[int]]              # [weekday 0=Mon][local hour] -> prompts
    days: List[date]                      # x axis of `daily`, oldest first
    daily: Dict[str, List[int]]           # provider -> prompts per day (aligned with `days`)
    active_days: int = 0
    models: List[Tuple[str, int]] = field(default_factory=list)  # (model, conversations)


def since_for(days: Optional[int], now: Optional[datetime] = None) -> Optional[str]:
    """ISO-8601 UTC lower bound for a "last N days" range (None = all time)."""
    if days is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


_PROMPT_STARTS_SQL = """
WITH b AS (
    SELECT conversation_id, seq, timestamp,
           (role = 'user' AND kind = 'text'
            AND ltrim(text, ' ' || char(9) || char(10) || char(13)) NOT LIKE '[Request interrupted%') AS p,
           (role = 'user' AND kind = 'text'
            AND ltrim(text, ' ' || char(9) || char(10) || char(13)) LIKE '[Request interrupted%') AS intr
    FROM messages
), t AS (
    SELECT conversation_id, timestamp, p, intr,
           LAG(p, 1, 0) OVER (PARTITION BY conversation_id ORDER BY seq) AS prev
    FROM b
)
SELECT conversation_id, timestamp, (p AND NOT prev) AS prompt_start, intr FROM t
WHERE (p AND NOT prev) OR intr
"""


def load_stats(conn: sqlite3.Connection, since: Optional[str] = None, provider: Optional[str] = None,
               provider_labels: Optional[Dict[str, str]] = None,
               today: Optional[date] = None) -> Stats:
    labels = provider_labels or {}
    rows = conn.execute(
        "SELECT c.*, p.path AS project_path, p.name AS project_name FROM conversations c "
        "JOIN projects p ON p.id = c.project_id"
    ).fetchall()

    # Roll subagents into their (top-level) parent.
    roots: Dict[Tuple[str, str], ConversationStats] = {}
    by_id: Dict[int, ConversationStats] = {}
    children: List[sqlite3.Row] = []
    for r in rows:
        if r["parent_external_id"]:
            children.append(r)
            continue
        c = ConversationStats(
            id=r["id"], project_id=r["project_id"], project_path=r["project_path"],
            project_name=r["project_name"], provider=r["provider"],
            title=r["title"] or _first_line(r["first_prompt"]),
            started_at=r["started_at"], updated_at=r["updated_at"], model=r["model"],
            input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
            cache_read_tokens=r["cache_read_tokens"], cache_write_tokens=r["cache_write_tokens"],
            cost_usd=r["cost_usd"], archived=bool(r["missing_from_source"]),
        )
        roots[(r["provider"], r["external_id"])] = c
        by_id[r["id"]] = c
    tools_by_conv: Dict[int, Dict[str, int]] = {}
    tool_names: Dict[str, str] = {}  # key -> display name (Claude's casing wins over OpenCode's lowercase)
    for r in rows:
        target = by_id.get(r["id"])
        if target is None:
            target = roots.get((r["provider"], r["parent_external_id"]))
            if target is None:
                continue
            target.subagents += 1
            for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                setattr(target, name, getattr(target, name) + r[name])
            if r["cost_usd"] is not None and target.cost_usd is None:
                target.cost_usd = 0.0
            if r["cost_usd"] is not None:
                target.cost_usd += r["cost_usd"]
            by_id[r["id"]] = target
        target.tool_calls += r["tool_call_count"]
        try:
            used = json.loads(r["tools_used"]) if r["tools_used"] else {}
        except ValueError:
            used = {}
        merged = tools_by_conv.setdefault(target.id, {})
        for name, n in used.items():
            key = _tool_key(name)
            if key not in tool_names or tool_names[key] == key:
                tool_names[key] = name
            merged[key] = merged.get(key, 0) + int(n)

    # Per-block counts (thinking, tool errors by tool).
    tool_errors_by_conv: Dict[int, Counter] = defaultdict(Counter)
    for r in conn.execute(
        "SELECT conversation_id, SUM(kind = 'thinking') AS thinking FROM messages GROUP BY conversation_id"
    ):
        c = by_id.get(r["conversation_id"])
        if c is not None:
            c.thinking += r["thinking"] or 0
    for r in conn.execute(
        "SELECT conversation_id, COALESCE(tool_name, '?') AS tool, COUNT(*) AS n FROM messages "
        "WHERE kind = 'tool_result' AND is_error = 1 GROUP BY conversation_id, tool"
    ):
        c = by_id.get(r["conversation_id"])
        if c is not None:
            c.tool_errors += r["n"]
            tool_errors_by_conv[c.id][_tool_key(r["tool"])] += r["n"]

    # Prompt starts (Q&A) + interrupts; prompt timestamps feed the activity charts.
    prompt_times: List[Tuple[ConversationStats, datetime]] = []
    for r in conn.execute(_PROMPT_STARTS_SQL):
        conv_id = r["conversation_id"]
        c = by_id.get(conv_id)
        if c is None or c.id != conv_id:  # subagent prompts come from the parent agent, not you
            continue
        if r["prompt_start"]:
            c.prompts += 1
            ts = parse_iso(r["timestamp"])
            if ts is not None:
                prompt_times.append((c, ts))
        if r["intr"]:
            c.interrupts += 1

    # Filter.
    convs = [c for c in roots.values()
             if (provider is None or c.provider == provider)
             and (since is None or (c.updated_at or c.started_at or "") >= since)]
    convs.sort(key=lambda c: c.updated_at or c.started_at or "", reverse=True)
    kept = {c.id for c in convs}

    total = Totals(key="", label="All")
    agents: Dict[str, Totals] = {}
    projects: Dict[str, Totals] = {}
    tools: Dict[str, ToolStats] = {}
    models: Counter = Counter()
    hist = [0] * len(QA_BUCKETS)
    for c in convs:
        total.add(c)
        agents.setdefault(c.provider, Totals(key=c.provider, label=labels.get(c.provider, c.provider))).add(c)
        projects.setdefault(c.project_path, Totals(key=c.project_path, label=c.project_name)).add(c)
        if c.model:
            models[c.model] += 1
        if c.prompts:
            hist[max(i for i, (_, lo) in enumerate(QA_BUCKETS) if c.prompts >= lo)] += 1
        errors = tool_errors_by_conv.get(c.id, Counter())
        for name, n in tools_by_conv.get(c.id, {}).items():
            t = tools.setdefault(name, ToolStats(name=_tool_display(tool_names.get(name, name))))
            t.calls += n
            t.conversations += 1
            t.errors += errors.get(name, 0)
            t.by_provider[c.provider] = t.by_provider.get(c.provider, 0) + n

    # Activity, by local time.
    since_dt = parse_iso(since + "Z") if since else None
    heatmap = [[0] * 24 for _ in range(7)]
    per_day: Dict[str, Counter] = defaultdict(Counter)
    active = set()
    for c, ts in prompt_times:
        if c.id not in kept or (since_dt is not None and ts < since_dt):
            continue
        local = ts.astimezone()
        heatmap[local.weekday()][local.hour] += 1
        per_day[c.provider][local.date()] += 1
        active.add(local.date())
    today = today or datetime.now().astimezone().date()
    if since_dt is not None:
        first = since_dt.astimezone().date()
    else:
        first = min(active) if active else today
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    daily = {p: [per_day[p].get(d, 0) for d in days] for p in sorted(per_day)}

    return Stats(
        since=since, total=total,
        agents=sorted(agents.values(), key=lambda t: -t.conversations),
        projects=sorted(projects.values(), key=lambda t: (-t.conversations, t.label)),
        conversations=convs,
        tools=sorted(tools.values(), key=lambda t: -t.calls),
        qa_histogram=[(label, n) for (label, _), n in zip(QA_BUCKETS, hist)],
        heatmap=heatmap, days=days, daily=daily, active_days=len(active),
        models=models.most_common(),
    )


def _tool_key(name: str) -> str:
    """Claude Code says "Bash", OpenCode "bash": one tool for the stats."""
    return name.lower()


def _tool_display(name: str) -> str:
    return name[:1].upper() + name[1:] if name.islower() and "_" not in name else name


def _first_line(text: Optional[str]) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return "(untitled)"
