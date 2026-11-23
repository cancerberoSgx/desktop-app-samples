"""Pure (wx-free) filtering + sorting for the conversations tree.

The tree is sorted here and repopulated in order rather than via
TreeListCtrl's native sort: on wxGTK 3.2 a custom TreeListItemComparator is
never called, so header clicks left the rows untouched.
"""
from typing import List, Optional, Sequence, Tuple

from app.models import Conversation

# (label, width, numeric) - numeric columns are right-aligned and sort
# descending first (biggest first), text columns ascending first.
CONVERSATION_COLUMNS = [
    ("Conversation", 240, False),
    ("Tool", 90, False),
    ("Last activity", 125, False),
    ("Prompts", 65, True),
    ("Tool calls", 75, True),
    ("Tokens", 70, True),
    ("Cost", 65, True),
]
LAST_ACTIVITY_COLUMN = 2


def total_tokens(conv: Conversation) -> int:
    return conv.input_tokens + conv.output_tokens + conv.cache_read_tokens + conv.cache_write_tokens


def sort_key(conv: Conversation, column: int):
    if column == 0:
        return conv.display_title.lower()
    if column == 1:
        return conv.provider
    if column == 2:
        return conv.updated_at or conv.started_at or ""
    if column == 3:
        return conv.user_message_count
    if column == 4:
        return conv.tool_call_count
    if column == 5:
        return total_tokens(conv)
    return conv.cost_usd or 0.0


def default_ascending(column: int) -> bool:
    """Direction for the first click on a column: text A-Z, while dates and
    numbers start newest / biggest first."""
    return not (CONVERSATION_COLUMNS[column][2] or column == LAST_ACTIVITY_COLUMN)


def title_matches(title: str, query: str) -> bool:
    """Case-insensitive; every word of `query` must appear, in any order."""
    lowered = title.lower()
    return all(word in lowered for word in query.lower().split())


def arrange(convs: Sequence[Conversation], query: str, column: int,
            ascending: bool) -> List[Tuple[Conversation, List[Conversation]]]:
    """Top-level conversations with their subagent children, filtered and
    sorted. A matching parent keeps all its children; a non-matching parent
    is kept (with just the matching children) if any child matches."""
    ids = {(c.provider, c.external_id) for c in convs}
    children = {}
    tops = []
    for c in convs:
        parent_key = (c.provider, c.parent_external_id) if c.parent_external_id else None
        if parent_key and parent_key in ids:
            children.setdefault(parent_key, []).append(c)
        else:
            tops.append(c)

    def ordered(items: List[Conversation]) -> List[Conversation]:
        return sorted(items, key=lambda c: sort_key(c, column), reverse=not ascending)

    result = []
    for top in ordered(tops):
        kids = children.get((top.provider, top.external_id), [])
        if query.strip() and not title_matches(top.display_title, query):
            kids = [k for k in kids if title_matches(k.display_title, query)]
            if not kids:
                continue
        result.append((top, ordered(kids)))
    return result


def header_label(column: int, sort_column: Optional[int], ascending: bool) -> str:
    label = CONVERSATION_COLUMNS[column][0]
    if column != sort_column:
        return label
    return f"{label} {'▲' if ascending else '▼'}"
