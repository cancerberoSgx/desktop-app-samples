import json
from typing import Any

import wx

MAX_JSON_CHILDREN = 200
MAX_PREVIEW_LEN = 60


def _preview(value: Any) -> str:
    """A JSON-faithful leaf preview (quoted strings, true/false/null, ...)
    rather than Python's repr, truncated so one huge scalar can't blow up
    a tree label."""
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= MAX_PREVIEW_LEN else text[:MAX_PREVIEW_LEN] + "..."


class JsonTreeCtrl(wx.TreeCtrl):
    """Renders a parsed JSON value (see
    DatasourceRepository.get_key_details / redis_value_format.fetch_json_value)
    as an expandable tree for the Json tab (see KeyDetailsDialog) - object
    keys and array indices become branches, scalars become leaves labeled
    "key: value". Built eagerly, unlike KeyTreeView's lazy key browser,
    since a single document is already fully in memory by the time this
    runs - each object/array level is still capped at MAX_JSON_CHILDREN so
    a pathologically large document can't stall the UI while the tree
    widget is built."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.BORDER_SUNKEN,
        )

    def set_value(self, value: Any) -> None:
        self.DeleteAllItems()
        # wx.TR_HIDE_ROOT forbids Expand()-ing the hidden root itself, so
        # expand the real top-level ("$") node it returns instead.
        root = self.AddRoot("root")
        top_item = self._add_node(root, "$", value)
        self.Expand(top_item)

    def _add_node(self, parent_item: wx.TreeItemId, label: str, value: Any) -> wx.TreeItemId:
        if isinstance(value, dict):
            item = self.AppendItem(parent_item, f"{label} {{{len(value)}}}")
            for index, (key, child) in enumerate(value.items()):
                if index >= MAX_JSON_CHILDREN:
                    self.AppendItem(item, f"... {len(value) - MAX_JSON_CHILDREN} more fields")
                    break
                self._add_node(item, key, child)
            return item
        if isinstance(value, list):
            item = self.AppendItem(parent_item, f"{label} [{len(value)}]")
            for index, child in enumerate(value):
                if index >= MAX_JSON_CHILDREN:
                    self.AppendItem(item, f"... {len(value) - MAX_JSON_CHILDREN} more items")
                    break
                self._add_node(item, f"[{index}]", child)
            return item
        return self.AppendItem(parent_item, f"{label}: {_preview(value)}")
