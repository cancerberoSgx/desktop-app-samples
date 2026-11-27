"""Pure (wx-free) sorting for the projects list (header clicks)."""
from typing import List, Optional, Sequence

from app.models import ProjectGroup

# (label, width, numeric) - numeric columns are right-aligned. Text columns
# sort A-Z first; numbers and dates sort biggest / newest first.
PROJECT_COLUMNS = [
    ("Project", 150, False),
    ("Conv.", 50, True),
    ("Last activity", 125, False),
    ("Tokens", 65, True),
    ("Cost", 65, True),
    ("Tools", 140, False),
]
PROJECT_LAST_ACTIVITY_COLUMN = 2


def project_sort_key(group: ProjectGroup, column: int):
    if column == 0:
        return (group.name.lower(), group.path.lower())
    if column == 1:
        return group.conversation_count
    if column == 2:
        return group.last_activity or ""
    if column == 3:
        return group.total_tokens
    if column == 4:
        # Unknown cost sorts as the cheapest.
        return group.cost_usd if group.cost_usd is not None else -1.0
    return ", ".join(group.providers)


def project_default_ascending(column: int) -> bool:
    return not (PROJECT_COLUMNS[column][2] or column == PROJECT_LAST_ACTIVITY_COLUMN)


def sort_projects(groups: Sequence[ProjectGroup], column: int, ascending: bool) -> List[ProjectGroup]:
    """Stable: ties keep the incoming (most recent first) order."""
    return sorted(groups, key=lambda g: project_sort_key(g, column), reverse=not ascending)


def project_header_label(column: int, sort_column: Optional[int], ascending: bool) -> str:
    label = PROJECT_COLUMNS[column][0]
    if column != sort_column:
        return label
    return f"{label} {'▲' if ascending else '▼'}"


def format_project_cost(group: ProjectGroup) -> str:
    """`$1.23`, with `*` when some conversations have no recorded cost
    (Claude Code without a `cost-state` line) - same marker as Stats."""
    if group.cost_usd is None:
        return "?" if group.cost_unknown else ""
    return f"${group.cost_usd:.2f}{'*' if group.cost_unknown else ''}"
