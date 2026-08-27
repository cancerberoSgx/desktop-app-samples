from typing import Dict, List

KEY_DELIMITER = ":"
NO_PREFIX_LABEL = "(no prefix)"


def new_node() -> Dict:
    return {"children": {}, "leaves": []}


def insert_key(root: Dict, key: str, delimiter: str = KEY_DELIMITER) -> str:
    """Insert one key into `root` (mutating it in place) and return the
    top-level branch label it landed under (or NO_PREFIX_LABEL). Only the
    segments before the last one become tree nodes - the last segment is
    never a node, it's a leaf key attached to its parent branch (e.g.
    "doc:foo:asdasd" creates branches "doc" and "doc:foo", with the full
    key as a leaf under "doc:foo"). Keys with no delimiter at all (no
    branch to belong to) are grouped under a synthetic top-level bucket so
    they aren't dropped.

    Split out from build_key_tree so a caller doing incremental updates
    (see KeyTreeView.add_keys) can insert one batch at a time and use the
    returned label to know which top-level tree item needs refreshing,
    without re-walking the whole tree on every batch."""
    parts = key.split(delimiter)
    if len(parts) < 2:
        node = root["children"].setdefault(NO_PREFIX_LABEL, new_node())
        node["leaves"].append(key)
        return NO_PREFIX_LABEL
    node = root
    for part in parts[:-1]:
        node = node["children"].setdefault(part, new_node())
    node["leaves"].append(key)
    return parts[0]


def build_key_tree(keys: List[str], delimiter: str = KEY_DELIMITER) -> Dict:
    """Group flat Redis keys into a tree of colon-delimited prefix
    branches in one pass - see insert_key for how a single key is placed."""
    root = new_node()
    for key in keys:
        insert_key(root, key, delimiter)
    return root
