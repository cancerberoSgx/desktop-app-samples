"""Turn raw Redis values into displayable text - shared by anything that
renders a key's value (currently KeyDetailsDialog) or a raw command's
result (currently ScriptsView). Kept separate from DatasourceRepository
because it's pure formatting, not I/O."""

from typing import Any, Tuple

MAX_TEXT_LEN = 4096
MAX_ELEMENTS = 200


def format_bytes_as_text(raw: bytes) -> str:
    """Render raw bytes as text. Some values (embeddings/vectors, RDB-style
    blobs, etc.) aren't valid UTF-8 - those fall back to a hex preview
    instead of raising or silently mangling the bytes."""
    if raw is None:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        preview = raw[:MAX_TEXT_LEN]
        hex_pairs = " ".join(f"{b:02x}" for b in preview)
        suffix = " ... (truncated)" if len(raw) > MAX_TEXT_LEN else ""
        return f"<binary, {len(raw)} bytes>\n{hex_pairs}{suffix}"
    if len(text) > MAX_TEXT_LEN:
        return text[:MAX_TEXT_LEN] + f"... (truncated, {len(text)} chars total)"
    return text


def build_value_text(client, key: str, redis_type: str) -> Tuple[str, bool]:
    """Fetch `key`'s value via the command appropriate for `redis_type` and
    render it as text, capped at MAX_ELEMENTS entries for collection types
    so a huge hash/list/set/zset can't stall the UI. Returns
    (text, truncated)."""
    if redis_type == "string":
        return format_bytes_as_text(client.get(key)), False

    if redis_type == "hash":
        items = []
        truncated = False
        for field, value in client.hscan_iter(key, count=1000):
            if len(items) >= MAX_ELEMENTS:
                truncated = True
                break
            items.append(f"{format_bytes_as_text(field)}: {format_bytes_as_text(value)}")
        return "\n".join(items), truncated

    if redis_type == "list":
        length = client.llen(key)
        raw_items = client.lrange(key, 0, MAX_ELEMENTS - 1)
        lines = [f"[{i}] {format_bytes_as_text(v)}" for i, v in enumerate(raw_items)]
        return "\n".join(lines), length > MAX_ELEMENTS

    if redis_type == "set":
        items = []
        truncated = False
        for member in client.sscan_iter(key, count=1000):
            if len(items) >= MAX_ELEMENTS:
                truncated = True
                break
            items.append(format_bytes_as_text(member))
        return "\n".join(items), truncated

    if redis_type == "zset":
        total = client.zcard(key)
        raw_items = client.zrange(key, 0, MAX_ELEMENTS - 1, withscores=True)
        lines = [f"{format_bytes_as_text(member)} (score={score})" for member, score in raw_items]
        return "\n".join(lines), total > MAX_ELEMENTS

    if redis_type == "stream":
        entries = client.xrange(key, count=MAX_ELEMENTS)
        lines = []
        for entry_id, fields in entries:
            entry_id_text = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
            field_text = ", ".join(
                f"{format_bytes_as_text(k)}={format_bytes_as_text(v)}" for k, v in fields.items()
            )
            lines.append(f"{entry_id_text}: {field_text}")
        return "\n".join(lines), len(entries) >= MAX_ELEMENTS

    return "", False


def format_command_result(value: Any) -> str:
    """Render the return value of a raw `client.execute_command(...)` call
    as text - used by the Scripts tab, where the command (and therefore
    the shape of its result: bool, int, bytes, nested list/dict of bytes,
    ...) isn't known ahead of time the way it is for build_value_text."""
    if value is None:
        return "(nil)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return format_bytes_as_text(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [format_command_result(v) for v in value]
        return "\n".join(items) if items else "(empty)"
    if isinstance(value, dict):
        items = [f"{format_command_result(k)}: {format_command_result(v)}" for k, v in value.items()]
        return "\n".join(items) if items else "(empty)"
    return str(value)
