# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A headless MCP server exposing OpenCV to LLM agents. Unlike its sibling apps
in this monorepo it is **not** a wxPython desktop app and is not frozen with
PyInstaller: it's a pure-Python package (src layout, `uv`, `uv_build`) with
a `my-image-mcp` console script. It must work with any MCP client (Claude,
OpenAI, ...), so keep tool schemas plain JSON Schema and avoid
client-specific features.

## Commands

```bash
uv sync                                  # install (locked)
uv run pytest -q                         # all tests, no display needed
uv run ruff check . && uv run ruff format --check .
uv run my-image-mcp tools                # list tools
uv run my-image-mcp --root DIR           # stdio server (default)
uv run my-image-mcp serve --transport streamable-http --port 8000
uv run my-image-mcp run pipeline.json    # replay an exported pipeline
```

## Architecture

- `ops/*.py` are plain, typed functions decorated with `@op("<module>")` and
  return `Result(outputs=[...], data={...})`. **The type hints are the MCP
  schema**: `Img` (np.ndarray) and `Contours` params become handle strings,
  `InPath`/`OutPath` are sandboxed paths, `Literal[...]` becomes an enum. The
  docstring is the tool description the model reads, so write it for the
  model.
- `core/engine.py` resolves handles, validates paths, calls the op, stores
  outputs, and records a `Step`. History plus the `producer` map power
  `export_pipeline` (JSON or Python) and `run_pipeline`. `cv_call` goes
  through the same engine path (special-cased as op `"cv_call"`).
- `server.py` turns every registry entry into a tool by synthesising an
  async wrapper with an explicit `__signature__`, then adds the
  session/pipeline/`cv_*` tools.
- MCP SDK is **2.x** (`mcp.server.mcpserver.MCPServer`, not v1's FastMCP).
  Any non-`ToolError` exception is hidden from the model as a generic error,
  which is why everything is converted with `_tool_error`/`_guard`.
- `core/cvbridge.py` holds the argument/return encoding for `cv_call` and
  the denylist (file/GUI/video I/O, and any name starting with
  read/write/save/load/open).

## Rules

- **At most 10 curated tools per module** (user decision); a test enforces
  it via tool titles. Things beyond that go through `cv_call`.
- Ops must never mutate their inputs: return copies. Stored handles are
  immutable, and `cv_call` copies arrays before passing them in.
- Color params are RGB strings parsed by `ops/_util.parse_color`, which
  returns BGR(A) scalars.
- Image inputs are local files only for now (no URLs, webcam or video). CPU
  only.
- Versioning follows the repo convention: `VERSION` is the source of truth,
  `pyproject.toml` must match (CI checks; `scripts/bump-app-version.py`
  updates both), and tags are `my-image-mcp-v*`.

## Planned (not done)

Feature detection / matching, object & face detection, DNN models (ONNX
from opencv_zoo with a model cache and a path-checked loader), video
frames, image resources (`image://` MCP resources), auth for HTTP, a visual
UI.
