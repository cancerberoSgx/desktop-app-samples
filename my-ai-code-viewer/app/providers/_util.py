import json
from datetime import datetime, timezone
from typing import Any, Optional

MAX_BLOCK_CHARS = 200_000


def clip(text: str, limit: int = MAX_BLOCK_CHARS) -> str:
    """Cap a single block's stored text - one giant tool output (a cat of a
    minified bundle, say) shouldn't bloat the archive or freeze the viewer."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated {len(text) - limit:,} characters ...]"


def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def ms_to_iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
