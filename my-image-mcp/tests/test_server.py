"""End-to-end tests through a real MCP client session (in-process transport)."""

from __future__ import annotations

import base64
import json
import re

import anyio
import cv2
import numpy as np
from mcp import Client

from my_image_mcp.config import Settings
from my_image_mcp.server import build_server

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # OpenAI / Anthropic tool-name constraint


def _run(fn):
    return anyio.run(fn)


def _json(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_tool_schemas_are_portable(root):
    async def main():
        async with Client(build_server(Settings(roots=[root]))) as c:
            tools = (await c.list_tools()).tools
            assert len(tools) > 50
            for t in tools:
                assert NAME_RE.match(t.name), t.name
                assert t.input_schema["type"] == "object"
                assert t.description
            by_module: dict[str, int] = {}
            for t in tools:
                if t.title and ":" in t.title:
                    mod = t.title.split(":")[0]
                    by_module[mod] = by_module.get(mod, 0) + 1
            assert all(n <= 10 for n in by_module.values()), by_module

    _run(main)


def test_end_to_end_session(root):
    async def main():
        async with Client(build_server(Settings(roots=[root]))) as c:
            img = _json(await c.call_tool("load_image", {"path": "shapes.png"}))["outputs"][0]["handle"]
            gray = _json(await c.call_tool("convert_color", {"image": img, "to": "gray"}))["outputs"][0]["handle"]
            th = _json(await c.call_tool("threshold", {"image": gray, "value": 60}))
            cnt = _json(await c.call_tool("find_contours", {"image": th["outputs"][0]["handle"]}))
            assert cnt["count"] == 3
            boxes = [c_["bbox"] for c_ in cnt["contours"]]
            drawn = _json(await c.call_tool("draw_boxes", {"image": img, "boxes": boxes, "labels": ["a", "b", "c"]}))[
                "outputs"
            ][0]["handle"]

            prev = await c.call_tool("preview", {"handle": drawn, "compare_with": [img], "max_size": 256})
            assert not prev.is_error
            image_block = next(b for b in prev.content if b.type == "image")
            decoded = cv2.imdecode(np.frombuffer(base64.b64decode(image_block.data), np.uint8), cv2.IMREAD_COLOR)
            assert decoded is not None and decoded.shape[1] > 256  # two tiles side by side

            saved = _json(await c.call_tool("save_image", {"image": drawn, "path": "out/annotated.png"}))
            assert (root / "out" / "annotated.png").exists() and saved["file_bytes"] > 0

            # Values the model copies out of JSON results (the boxes) are literals: draw_boxes depends
            # only on the loaded image. Handle inputs (contours) carry the full lineage.
            exported = await c.call_tool("export_pipeline", {"handles": [drawn], "format": "json"})
            assert [s["op"] for s in json.loads(exported.content[0].text)["steps"]] == ["load_image", "draw_boxes"]
            cnt_handle = cnt["outputs"][0]["handle"]
            outlined = _json(await c.call_tool("draw_contours", {"image": img, "contours": cnt_handle}))
            exported = await c.call_tool("export_pipeline", {"handles": [outlined["outputs"][0]["handle"], drawn]})
            steps = json.loads(exported.content[0].text)["steps"]
            assert [s["op"] for s in steps] == [
                "load_image",
                "convert_color",
                "threshold",
                "find_contours",
                "draw_boxes",
                "draw_contours",
            ]
            # defaults are not recorded
            assert steps[2]["args"] == {"image": gray, "value": 60}

            replay = _json(await c.call_tool("run_pipeline", {"steps": steps}))
            assert replay["ok"]

    _run(main)


def test_errors_reach_the_model(root):
    async def main():
        async with Client(build_server(Settings(roots=[root]))) as c:
            r = await c.call_tool("blur", {"image": "img_42"})
            assert r.is_error and "Unknown handle 'img_42'" in r.content[0].text
            r = await c.call_tool("load_image", {"path": "/etc/hostname"})
            assert r.is_error and "outside the allowed roots" in r.content[0].text
            r = await c.call_tool("cv_call", {"function": "imread", "args": ["x.png"]})
            assert r.is_error and "not available" in r.content[0].text

    _run(main)


def test_cv_discovery(root):
    async def main():
        async with Client(build_server(Settings(roots=[root]))) as c:
            found = json.loads((await c.call_tool("cv_search", {"query": "houghcircles"})).content[0].text)["results"]
            assert any(f["name"] == "HoughCircles" for f in found)
            doc = (await c.call_tool("cv_doc", {"function": "HoughCircles"})).content[0].text
            assert "HoughCircles" in doc
            info = _json(await c.call_tool("opencv_info", {}))
            assert info["opencv_version"] == cv2.__version__

    _run(main)
