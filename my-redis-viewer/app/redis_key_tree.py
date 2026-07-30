from typing import Dict, List

KEY_DELIMITER = ":"
NO_PREFIX_LABEL = "(no prefix)"


def new_node() -> Dict:
    return {"children": {}, "leaves": []}


def build_key_tree(keys: List[str], delimiter: str = KEY_DELIMITER) -> Dict:
    """Group flat Redis keys into a tree of colon-delimited prefix
    "branches". Only the segments before the last one become tree nodes -
    the last segment is never a node, it's a leaf key attached to its
    parent branch (e.g. "doc:foo:asdasd" creates branches "doc" and
    "doc:foo", with the full key as a leaf under "doc:foo"). Keys with no
    delimiter at all (no branch to belong to) are grouped under a
    synthetic top-level bucket so they aren't dropped."""
    root = new_node()
    for key in keys:
        parts = key.split(delimiter)
        if len(parts) < 2:
            node = root["children"].setdefault(NO_PREFIX_LABEL, new_node())
            node["leaves"].append(key)
            continue
        node = root
        for part in parts[:-1]:
            node = node["children"].setdefault(part, new_node())
        node["leaves"].append(key)
    return root
