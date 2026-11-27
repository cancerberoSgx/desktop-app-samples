"""MCP server: exposes registered ops plus session, pipeline and generic
OpenCV tools. Transport-agnostic (stdio, streamable HTTP, SSE), so it works
with any MCP client (Claude Code, Claude Desktop, OpenAI Agents SDK / Codex,
Cursor, ...)."""

from __future__ import annotations

import functools
import inspect
import json
import logging
from typing import Annotated, Any, Literal

import anyio
import cv2
import numpy as np
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import __version__, ops  # noqa: F401  (ops import registers all ops)
from .config import Settings
from .core import cvbridge
from .core.engine import Engine, dumps
from .core.registry import REGISTRY, OpSpec, Param
from .ops import _magick, _vips
from .ops._util import COLOR_HELP, to_bgr, to_uint8

log = logging.getLogger("my_image_mcp")

INSTRUCTIONS = """\
OpenCV image processing server. Images live in server memory and are referenced by handles.

Workflow:
1. load_image(path) -> returns a handle such as "img_1" (paths are relative to the server root).
2. Apply ops; every op takes handles and returns NEW handles (inputs are never modified), plus
   measurements as JSON. Contour sets get "cnt_N" handles.
3. Call preview(handle) to actually look at a result (compare_with shows several side by side).
   Only preview when you need to see it; measurements are often enough.
4. save_image(handle, path) to write results; export_pipeline(handles) to get a reproducible
   JSON pipeline or Python script; run_pipeline(steps) to run many ops in one call.

Conventions: pixel data is BGR (OpenCV); color parameters are RGB strings like "#ff0000" or "red".
Coordinates are pixels, origin top-left, x to the right, y down.

Anything not covered by a dedicated tool: cv_search("keyword") to find OpenCV functions,
cv_doc("name") for its signature, then cv_call("name", args, kwargs) passing handles as strings,
constants as "cv2.NAME", arrays as {"$array": [...], "dtype": "float32"}.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
CREATES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


# ---------------------------------------------------------------- op -> tool


def _tool_param(p: Param) -> inspect.Parameter:
    if p.handle_kind:
        what = f"{'list of ' if p.handle_list else ''}{p.handle_kind} handle{'s' if p.handle_list else ''}"
        example = "img_1" if p.handle_kind == "image" else "cnt_1"
        base: Any = list[str] if p.handle_list else str
        if p.optional:
            base = base | None
        ann = Annotated[base, Field(description=f"{what.capitalize()} (e.g. '{example}')")]
    elif p.path_role is not None:
        desc = "File path; relative paths resolve against the server root"
        ann = Annotated[str | None if p.optional else str, Field(description=desc)]
    elif p.name in ("color", "background", "border_color"):
        base = p.annotation
        ann = Annotated[base, Field(description=COLOR_HELP)]
    else:
        ann = p.annotation
    return inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY, default=p.default, annotation=ann)


def _tool_error(e: Exception) -> ToolError:
    if isinstance(e, ToolError):
        return e
    msg = str(e) or type(e).__name__
    return ToolError(msg if type(e).__name__ in ("OpError", "KeyError", "ValueError") else f"{type(e).__name__}: {msg}")


def _make_op_tool(engine: Engine, spec: OpSpec):
    defaults = {p.name: p.default for p in spec.params if p.default is not inspect.Parameter.empty}

    async def tool(**kwargs: Any) -> dict[str, Any]:
        # Only record arguments that differ from defaults: keeps history and exports readable.
        args = {k: v for k, v in kwargs.items() if k not in defaults or v != defaults[k]}
        try:
            return await anyio.to_thread.run_sync(engine.execute, spec.name, args)
        except Exception as e:
            raise _tool_error(e) from e

    tool.__name__ = spec.name
    tool.__doc__ = spec.description
    tool.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [_tool_param(p) for p in spec.params], return_annotation=dict[str, Any]
    )
    return tool


def _guard(fn):
    """Turn any exception into a ToolError so the model sees the message."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                return await fn(*a, **kw)
            except Exception as e:
                raise _tool_error(e) from e
    else:

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:
                raise _tool_error(e) from e

    return wrapper


# ---------------------------------------------------------------- preview


def _render(engine: Engine, handle: str) -> np.ndarray:
    kind = engine.store.kind(handle)
    if kind is None:
        raise KeyError(f"Unknown handle '{handle}'.")
    value = engine.store.get(handle)
    if kind == "contours":
        pts = np.concatenate([c.reshape(-1, 2) for c in value]) if len(value) else np.zeros((1, 2), int)
        w, h = int(pts[:, 0].max()) + 10, int(pts[:, 1].max()) + 10
        canvas = np.zeros((max(h, 32), max(w, 32), 3), np.uint8)
        cv2.drawContours(canvas, list(value), -1, (0, 255, 0), 1)
        return canvas
    if kind in ("image", "array") and isinstance(value, np.ndarray) and value.ndim in (2, 3):
        img = value
        if img.ndim == 3 and img.shape[2] not in (1, 3, 4):
            img = img[:, :, 0]
        img = to_uint8(img)
        if img.ndim == 3 and img.shape[2] == 4:  # composite alpha over a checkerboard
            h, w = img.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w]
            board = np.where(((yy // 16 + xx // 16) % 2)[..., None] == 0, 200, 255).astype(np.float32)
            a = img[:, :, 3:4].astype(np.float32) / 255
            img = (img[:, :, :3] * a + board * (1 - a)).astype(np.uint8)
        return to_bgr(img)
    raise TypeError(f"Handle '{handle}' ({kind}) can't be previewed.")


def _fit(img: np.ndarray, max_w: int, max_h: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = min(max_w / w, max_h / h, 1.0)
    if s < 1.0:
        img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    return img


def _compose(engine: Engine, handles: list[str], max_size: int) -> np.ndarray:
    if len(handles) == 1:
        return _fit(_render(engine, handles[0]), max_size, max_size)
    n = len(handles)
    tile_w = max(64, (max_size * 2) // n)  # side-by-side strips may be wider than one preview
    tiles = []
    for h in handles:
        t = _fit(_render(engine, h), tile_w, max_size)
        label = np.full((22, t.shape[1], 3), 32, np.uint8)
        cv2.putText(label, h, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(np.vstack([label, t]))
    height = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, height - t.shape[0], 0, 4, cv2.BORDER_CONSTANT, value=(0, 0, 0)) for t in tiles]
    return np.hstack(tiles)


# ---------------------------------------------------------------- server


def build_server(settings: Settings | None = None, engine: Engine | None = None) -> MCPServer:
    engine = engine or Engine(settings or Settings.from_env())
    settings = engine.settings
    mcp = MCPServer(name="my-image-mcp", version=__version__, instructions=INSTRUCTIONS)

    for spec in REGISTRY.values():
        read_only = spec.name in ("histogram", "contour_properties")
        mcp.add_tool(
            _make_op_tool(engine, spec),
            name=spec.name,
            title=f"{spec.module}: {spec.name.replace('_', ' ')}",
            description=spec.description,
            annotations=READ_ONLY if read_only else CREATES,
            structured_output=False,
        )

    # ---- session ---------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def list_images() -> dict[str, Any]:
        """List every handle in memory (images, contour sets, arrays, objects) with size and origin."""
        from .core.store import describe

        items = []
        for it in engine.store.items():
            entry = {"handle": it.handle, "kind": it.kind, **describe(it.kind, it.value)}
            step = engine.producer.get(it.handle)
            if step is not None:
                entry["created_by"] = engine.history[step].op
            items.append(entry)
        return {
            "items": items,
            "memory_mb": round(engine.store.total_bytes / 2**20, 2),
            "limit_mb": settings.max_store_mb,
        }

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def image_info(handle: str) -> dict[str, Any]:
        """Size, dtype, per-channel min/max/mean/std and the op that created a handle."""
        return engine.handle_info(handle)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def preview(
        handle: str,
        compare_with: list[str] | None = None,
        max_size: int | None = None,
        format: Literal["jpeg", "png"] = "jpeg",
    ) -> list[Any]:
        """Return a downscaled view of an image so you can look at it. compare_with adds more handles
        side by side (labelled), e.g. before/after. Float/16-bit/label images are normalised for display."""
        handles = [handle] + list(compare_with or [])
        img = _compose(engine, handles, max_size or settings.preview_max_size)
        ext = ".jpg" if format == "jpeg" else ".png"
        ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, 85] if format == "jpeg" else [])
        if not ok:
            raise ValueError("Could not encode preview")
        meta = {h: engine.handle_info(h) for h in handles}
        for m in meta.values():
            m.pop("created_by", None)
        return [Image(data=buf.tobytes(), format=format), json.dumps(meta, default=str)]

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, openWorldHint=False), structured_output=False)
    @_guard
    def delete_images(handles: list[str]) -> dict[str, Any]:
        """Free memory by deleting handles. Their history is kept, so pipelines can still be exported."""
        return {"deleted": [h for h in handles if engine.store.delete(h)]}

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def opencv_info(include_build_info: bool = False) -> dict[str, Any]:
        """OpenCV version, available contrib modules, optional backends (libvips, ImageMagick),
        server roots and limits."""
        modules = sorted(n for n in dir(cv2) if isinstance(getattr(cv2, n), type(cv2)) and not n.startswith("_"))
        info = {
            "server_version": __version__,
            "opencv_version": cv2.__version__,
            "numpy_version": np.__version__,
            "libvips_version": _vips.VERSION,
            "imagemagick_version": _magick.version(),
            "modules": modules,
            "threads": cv2.getNumThreads(),
            "roots": [str(r) for r in settings.roots],
            "allow_any_path": settings.allow_any_path,
            "max_store_mb": settings.max_store_mb,
        }
        if include_build_info:
            info["build_info"] = cv2.getBuildInformation()
        return info

    # ---- pipeline --------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def history(limit: int = 50) -> dict[str, Any]:
        """The most recent recorded steps (op, args, outputs) of this session."""
        steps = engine.history[-limit:]
        return {"total_steps": len(engine.history), "steps": [{"index": s.index, **s.to_json()} for s in steps]}

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def export_pipeline(handles: list[str], format: Literal["json", "python"] = "json", path: str | None = None) -> str:
        """Export the steps that produced the given handles as a replayable JSON pipeline
        (for run_pipeline or `my-image-mcp run`) or a Python script. Optionally write it to path."""
        text = dumps(engine.export_json(handles)) if format == "json" else engine.export_python(handles)
        if path:
            p = settings.resolve_path(path, write=True)
            p.write_text(text, encoding="utf-8")
            return f"Written to {p}\n\n{text}"
        return text

    @mcp.tool(annotations=CREATES, structured_output=False)
    @_guard
    async def run_pipeline(
        steps: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description='[{"op": "blur", "args": {"image": "img_1", "ksize": 5}, "outputs": ["b"]}, '
                '{"op": "canny", "args": {"image": "b"}}] - outputs names are optional aliases; '
                '"$prev" is the previous step\'s first output. cv_call steps use args '
                '{"function", "args", "kwargs"}.'
            ),
        ] = None,
        path: Annotated[str | None, Field(description="Load the pipeline from a JSON file instead")] = None,
    ) -> dict[str, Any]:
        """Run several ops in one call, stopping at the first error. Accepts the export_pipeline JSON format."""
        if path:
            data = json.loads(settings.resolve_path(path).read_text(encoding="utf-8"))
            steps = data["steps"] if isinstance(data, dict) else data
        if not steps:
            raise ValueError("Give steps or path.")
        return await anyio.to_thread.run_sync(engine.run_pipeline, steps)

    # ---- generic OpenCV ----------------------------------------------------

    @mcp.tool(annotations=CREATES, structured_output=False)
    @_guard
    async def cv_call(
        function: Annotated[str, Field(description="e.g. 'GaussianBlur', 'ximgproc.niBlackThreshold', 'obj_2.detect'")],
        args: Annotated[list[Any] | None, Field(description="Positional arguments")] = None,
        kwargs: Annotated[dict[str, Any] | None, Field(description="Keyword arguments")] = None,
    ) -> dict[str, Any]:
        """Call any OpenCV function (or a method of a stored OpenCV object) directly.
        Argument encoding: handle strings ('img_3') -> stored values (copied, never modified);
        'cv2.THRESH_BINARY | cv2.THRESH_OTSU' -> constants; {"$array": [[...]], "dtype": "float32"} -> numpy;
        {"$tuple": [...]} -> tuple. Returned images/large arrays/objects become new handles; small values are
        returned inline. File, GUI and video I/O functions are blocked."""
        payload = {"function": function, "args": args or [], "kwargs": kwargs or {}}
        return await anyio.to_thread.run_sync(engine.execute, "cv_call", payload)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def cv_search(query: str, limit: int = 40) -> dict[str, Any]:
        """Search OpenCV functions/classes by name substring (case-insensitive), including contrib modules
        (e.g. 'thresh', 'feature', 'ximgproc', 'hough'). Returns names and signatures for cv_call."""
        return {"results": cvbridge.search(query, limit)}

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @_guard
    def cv_doc(function: str) -> str:
        """Full OpenCV docstring (signatures and parameter notes) for a function, e.g. 'HoughCircles'."""
        return cvbridge.doc(function)

    return mcp
