"""Execution engine: resolves handles, runs ops, stores outputs and records
every step so any result can be exported as a reproducible pipeline."""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .. import __version__
from ..config import Settings
from . import cvbridge
from .registry import REGISTRY, OpSpec
from .store import Store, describe
from .types import kind_of

PIPELINE_FORMAT = "my-image-mcp/pipeline"
CV_CALL = "cv_call"


class OpError(Exception):
    pass


@dataclass
class Step:
    index: int
    op: str
    args: dict[str, Any]  # canonical args: real handles, absolute paths
    inputs: list[str]
    outputs: list[str]
    output_paths: list[list] = field(default_factory=list)  # cv_call only: where each output sits in the return value
    time: float = 0.0

    def to_json(self) -> dict[str, Any]:
        d = {"op": self.op, "args": self.args, "outputs": self.outputs}
        if self.output_paths:
            d["output_paths"] = self.output_paths
        return d


def _identifier(name: str) -> str:
    ident = re.sub(r"\W", "_", name)
    return ident if ident and not ident[0].isdigit() else f"v_{ident}"


class Engine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.store = Store(self.settings.max_store_mb * 1024 * 1024)
        self.history: list[Step] = []
        self.producer: dict[str, int] = {}  # handle -> index of the step that created it
        # image handle -> index of the last drawing step that took it as input.
        # Drawing ops return a new image, so a second drawing on the same input
        # silently drops the first one; we warn the model when that happens.
        self.drawn_on: dict[str, int] = {}

    # ------------------------------------------------------------ handles

    def _ref(self, name: Any, aliases: dict[str, str], kind: str | None) -> str:
        if not isinstance(name, str):
            raise OpError(f"Expected a handle string, got {name!r}.")
        real = aliases.get(name, name)
        if real not in self.store:
            raise OpError(f"Unknown handle '{name}'. Use list_images to see available handles.")
        if kind and self.store.kind(real) != kind:
            raise OpError(f"Handle '{name}' is a {self.store.kind(real)}, expected a {kind}.")
        return real

    # ------------------------------------------------------------ execution

    def execute(self, op: str, args: dict[str, Any] | None, aliases: dict[str, str] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        aliases = aliases or {}
        if op == CV_CALL:
            return self._execute_cv_call(args, aliases)
        spec = REGISTRY.get(op)
        if spec is None:
            raise OpError(f"Unknown op '{op}'.")
        return self._execute_op(spec, args, aliases)

    def _execute_op(self, spec: OpSpec, args: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
        known = {p.name for p in spec.params}
        unknown = set(args) - known
        if unknown:
            raise OpError(f"{spec.name}: unknown argument(s) {sorted(unknown)}; expected {sorted(known)}.")

        call_args: dict[str, Any] = {}
        canonical: dict[str, Any] = {}
        inputs: list[str] = []
        for p in spec.params:
            if p.name not in args:
                if p.default is inspect.Parameter.empty:
                    raise OpError(f"{spec.name}: missing required argument '{p.name}'.")
                continue
            value = args[p.name]
            if p.handle_kind and value is not None:
                if p.handle_list:
                    reals = [self._ref(v, aliases, p.handle_kind) for v in value]
                    call_args[p.name] = [self.store.get(r) for r in reals]
                    canonical[p.name] = reals
                    inputs += reals
                else:
                    real = self._ref(value, aliases, p.handle_kind)
                    call_args[p.name] = self.store.get(real)
                    canonical[p.name] = real
                    inputs.append(real)
            elif p.path_role is not None:
                resolved = str(self.settings.resolve_path(value, write=p.path_role.write))
                call_args[p.name] = canonical[p.name] = resolved
            else:
                call_args[p.name] = canonical[p.name] = value

        try:
            result = spec.func(**call_args)
        except cv2.error as e:
            raise OpError(f"{spec.name}: OpenCV error: {str(e).strip().splitlines()[-1]}") from e
        except (ValueError, TypeError) as e:
            raise OpError(f"{spec.name}: {e}") from e

        outputs = []
        handles = []
        for value in result.outputs:
            kind = kind_of(value)
            h = self.store.put(kind, value)
            handles.append(h)
            outputs.append({"handle": h, "kind": kind, **describe(kind, value)})
        warning = self._redraw_warning(spec, canonical)
        self._record(spec.name, canonical, inputs, handles)
        if spec.module == "drawing" and isinstance(canonical.get("image"), str):
            self.drawn_on[canonical["image"]] = self.history[-1].index
        response: dict[str, Any] = {}
        if outputs:
            response["outputs"] = outputs
        response.update(result.data)
        if warning:
            response["warning"] = warning
        return response

    def _redraw_warning(self, spec: OpSpec, canonical: dict[str, Any]) -> str | None:
        """Warn when a drawing op is applied to an image another drawing op
        already used as input: the new result won't contain that drawing (the
        usual cause is parallel drawing calls on the same handle)."""
        if spec.module != "drawing":
            return None
        image = canonical.get("image")
        prev = self.drawn_on.get(image) if isinstance(image, str) else None
        if prev is None:
            return None
        step = self.history[prev]
        drawn = step.outputs[0] if step.outputs else "?"
        return (
            f"{image} was already drawn on by {step.op} (-> {drawn}); this result does NOT include that drawing. "
            f"If you meant to add to it, draw on {drawn} instead: chain drawing calls one after another "
            f'(never in parallel on the same handle), or use run_pipeline with "$prev".'
        )

    def _execute_cv_call(self, args: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
        function = args.get("function")
        if not function:
            raise OpError("cv_call: 'function' is required.")
        inputs: list[str] = []

        def is_handle(s: str) -> bool:
            return aliases.get(s, s) in self.store

        def lookup(s: str) -> Any:
            real = aliases.get(s, s)
            inputs.append(real)
            value = self.store.get(real)
            # Many cv2 functions draw in place; stored values must stay immutable.
            return value.copy() if isinstance(value, np.ndarray) else value

        def canonicalize(v: Any) -> Any:
            if isinstance(v, str) and is_handle(v):
                return aliases.get(v, v)
            if isinstance(v, list):
                return [canonicalize(x) for x in v]
            if isinstance(v, dict):
                return {k: canonicalize(x) for k, x in v.items()}
            return v

        fn, path = cvbridge.resolve_function(function, lookup)
        if path.startswith("obj_"):
            path = aliases.get(path.split(".")[0], path.split(".")[0]) + "." + path.split(".", 1)[1]
        pos = [cvbridge.decode_arg(a, lookup, is_handle) for a in args.get("args") or []]
        kw = {k: cvbridge.decode_arg(v, lookup, is_handle) for k, v in (args.get("kwargs") or {}).items()}
        try:
            ret = fn(*pos, **kw)
        except cv2.error as e:
            raise OpError(f"cv2.{path}: OpenCV error: {str(e).strip().splitlines()[-1]}") from e
        except Exception as e:  # cv2 raises plain TypeError/SystemError for bad overloads
            raise OpError(f"cv2.{path}: {type(e).__name__}: {e}") from e

        handles: list[str] = []
        paths: list[list] = []

        def store_fn(kind: str, value: Any, p: tuple) -> str:
            h = self.store.put(kind, value)
            handles.append(h)
            paths.append(list(p))
            return h

        encoded = cvbridge.encode_result(ret, store_fn)
        canonical = {
            "function": path,
            "args": canonicalize(args.get("args") or []),
            "kwargs": canonicalize(args.get("kwargs") or {}),
        }
        self._record(CV_CALL, canonical, inputs, handles, paths)
        return {"result": encoded}

    def _record(self, op: str, args: dict, inputs: list[str], outputs: list[str], paths: list | None = None) -> None:
        step = Step(len(self.history), op, args, sorted(set(inputs)), outputs, paths or [], time.time())
        self.history.append(step)
        for h in outputs:
            self.producer[h] = step.index

    # ------------------------------------------------------------ pipelines

    def run_pipeline(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Run steps in order. Each step is ``{"op": ..., "args": {...},
        "outputs": ["name", ...]}``; ``outputs`` names are optional aliases
        later steps can reference. ``"$prev"`` refers to the previous step's
        first output."""
        aliases: dict[str, str] = {}
        results = []
        prev: str | None = None
        for i, step in enumerate(steps):
            op = step.get("op")
            args = step.get("args") or {}
            if prev:
                aliases["$prev"] = prev
            try:
                res = self.execute(op, args, aliases)
            except Exception as e:
                return {"ok": False, "failed_step": i, "error": str(e), "results": results, "aliases": aliases}
            produced = [o["handle"] for o in res.get("outputs", [])]
            if op == CV_CALL:
                produced = self.history[-1].outputs
            for name, real in zip(step.get("outputs") or [], produced):
                aliases[name] = real
            if produced:
                prev = produced[0]
            results.append({"step": i, "op": op, **res})
        return {"ok": True, "results": results, "aliases": aliases, "final": prev}

    def lineage(self, handles: list[str]) -> list[Step]:
        needed: set[int] = set()
        stack = list(handles)
        while stack:
            h = stack.pop()
            idx = self.producer.get(h)
            if idx is None or idx in needed:
                continue
            needed.add(idx)
            stack.extend(self.history[idx].inputs)
        return [self.history[i] for i in sorted(needed)]

    def export_json(self, handles: list[str]) -> dict[str, Any]:
        missing = [h for h in handles if h not in self.producer]
        if missing:
            raise OpError(f"No recorded history for {missing}.")
        return {
            "format": PIPELINE_FORMAT,
            "version": 1,
            "generator": f"my-image-mcp {__version__}",
            "opencv": cv2.__version__,
            "targets": handles,
            "steps": [s.to_json() for s in self.lineage(handles)],
        }

    def export_python(self, handles: list[str]) -> str:
        steps = self.lineage(handles)
        lines = [
            '"""Pipeline exported by my-image-mcp ' + __version__ + f' (OpenCV {cv2.__version__})."""',
            "import cv2",
            "import numpy as np",
            "",
            "from my_image_mcp import ops",
            "",
        ]
        for s in steps:
            if s.op == CV_CALL:
                lines += _python_cv_call(s)
            else:
                lines.append(_python_op(s))
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------ info

    def handle_info(self, handle: str) -> dict[str, Any]:
        kind = self.store.kind(handle)
        if kind is None:
            raise OpError(f"Unknown handle '{handle}'.")
        value = self.store.get(handle)
        info = {"handle": handle, "kind": kind, **describe(kind, value)}
        if kind == "image":
            img = value
            chans = cv2.split(img) if img.ndim == 3 else [img]
            info["channel_stats"] = [
                {
                    "min": float(c.min()),
                    "max": float(c.max()),
                    "mean": round(float(c.mean()), 3),
                    "std": round(float(c.std()), 3),
                }
                for c in chans
            ]
            info["nonzero_ratio"] = round(float(np.count_nonzero(img)) / img.size, 4)
        idx = self.producer.get(handle)
        if idx is not None:
            step = self.history[idx]
            info["created_by"] = {"step": idx, "op": step.op, "args": step.args}
        return info


def _py_value(v: Any) -> str:
    if isinstance(v, str):
        return _identifier(v) if re.match(r"^(img|cnt|arr|seq|obj)_\d+$", v) else repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_py_value(x) for x in v) + "]"
    return repr(v)


def _python_op(s: Step) -> str:
    from .registry import REGISTRY

    spec = REGISTRY[s.op]
    handle_params = {p.name for p in spec.params if p.handle_kind}
    parts = []
    for k, v in s.args.items():
        if k in handle_params:
            parts.append(f"{k}={_py_value(v)}")
        else:
            parts.append(f"{k}={v!r}")
    call = f"ops.{s.op}({', '.join(parts)})"
    outs = [_identifier(h) for h in s.outputs]
    if not outs:
        return call
    if len(outs) == 1:
        return f"{outs[0]} = {call}.image"
    return f"{', '.join(outs)} = {call}.outputs"


def _py_cv_arg(v: Any) -> str:
    if isinstance(v, str):
        if re.match(r"^(img|cnt|arr|seq|obj)_\d+$", v):
            return _identifier(v)
        if cvbridge.resolve_constant(v) is not None and (v.startswith(("cv2.", "cv.")) or "|" in v):
            return " | ".join("cv2." + t.strip().split(".", 1)[-1] for t in re.split(r"[|+]", v))
        return repr(v)
    if isinstance(v, dict):
        if "$array" in v:
            dt = f", dtype=np.{v['dtype']}" if v.get("dtype") else ""
            return f"np.array({v['$array']!r}{dt})"
        if "$tuple" in v:
            return "(" + ", ".join(_py_cv_arg(x) for x in v["$tuple"]) + ",)"
        return "{" + ", ".join(f"{k!r}: {_py_cv_arg(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_py_cv_arg(x) for x in v) + "]"
    return repr(v)


def _python_cv_call(s: Step) -> list[str]:
    fn = s.args["function"]
    target = _identifier(fn.split(".")[0]) + "." + fn.split(".", 1)[1] if fn.startswith("obj_") else f"cv2.{fn}"
    parts = [_py_cv_arg(a) for a in s.args.get("args", [])]
    parts += [f"{k}={_py_cv_arg(v)}" for k, v in s.args.get("kwargs", {}).items()]
    call = f"{target}({', '.join(parts)})"
    if not s.outputs:
        return [call]
    if len(s.outputs) == 1 and s.output_paths == [[]]:
        return [f"{_identifier(s.outputs[0])} = {call}"]
    lines = [f"_r = {call}"]
    for h, p in zip(s.outputs, s.output_paths):
        lines.append(f"{_identifier(h)} = _r" + "".join(f"[{i!r}]" for i in p))
    return lines


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)
