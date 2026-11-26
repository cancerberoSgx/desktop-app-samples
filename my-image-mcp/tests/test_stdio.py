"""Launch the real CLI as a subprocess over stdio, the way MCP clients do."""

from __future__ import annotations

import json
import sys

import anyio
from mcp import Client, StdioServerParameters


def test_stdio_subprocess(root):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "my_image_mcp", "--root", str(root)],
    )

    async def main():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert {"load_image", "preview", "cv_call", "run_pipeline"} <= names
            r = await c.call_tool("load_image", {"path": "shapes.png"})
            assert not r.is_error
            assert json.loads(r.content[0].text)["outputs"][0]["width"] == 320

    anyio.run(main)
