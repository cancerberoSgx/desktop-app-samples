from datetime import datetime
from typing import Optional


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_datetime(value: Optional[str]) -> str:
    """ISO-8601 UTC -> local 'YYYY-MM-DD HH:MM' (string-sortable)."""
    dt = parse_iso(value)
    if dt is None:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def format_duration(start: Optional[str], end: Optional[str]) -> str:
    a, b = parse_iso(start), parse_iso(end)
    if a is None or b is None:
        return ""
    seconds = int((b - a).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_cost(cost: Optional[float]) -> str:
    if cost is None:
        return ""
    return f"${cost:.2f}"
