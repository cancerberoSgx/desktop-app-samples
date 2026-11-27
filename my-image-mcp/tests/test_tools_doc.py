"""Every tool must be described and tagged in scripts/gen_tools_doc.py (the
source of docs/tools.html)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import anyio

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_tools_doc.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_tools_doc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_tool_is_catalogued_and_tagged():
    gen = _load()
    tools = anyio.run(gen.fetch_tools)
    missing = sorted(t.name for t in tools if t.name not in gen.CATALOG)
    assert not missing, f"add these tools to CATALOG in scripts/gen_tools_doc.py: {missing}"
    for name, (tags, summary) in gen.CATALOG.items():
        assert tags and all(t in gen.TAGS for t in tags), (name, tags)
        assert len(summary) > 20, name


def test_catalog_has_no_stale_entries():
    from my_image_mcp.core.registry import REGISTRY

    gen = _load()
    known = set(REGISTRY) | set(gen.SERVER_MODULE)
    # magick tools are only registered when ImageMagick is installed
    stale = [n for n in gen.CATALOG if n not in known and n not in _magick_tool_names()]
    assert not stale, f"remove from CATALOG (no such tool): {stale}"


def _magick_tool_names() -> set[str]:
    import ast

    src = (Path(__file__).resolve().parent.parent / "src/my_image_mcp/ops/magick.py").read_text()
    names = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and any(
            isinstance(d, ast.Call) and getattr(d.func, "id", "") == "_op" for d in node.decorator_list
        ):
            names.add(node.name)
    return names


def test_render_produces_a_card_per_tool():
    gen = _load()
    tools = anyio.run(gen.fetch_tools)
    page = gen.render(gen.build_entries(tools))
    assert page.count('<article class="tool"') == len(tools)
