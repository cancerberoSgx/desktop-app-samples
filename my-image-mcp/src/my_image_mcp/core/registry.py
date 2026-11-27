"""Op registry.

Ops are plain, typed Python functions decorated with :func:`op`. The type
hints drive everything else:

* ``np.ndarray`` / ``Contours`` parameters become handle strings in the MCP
  schema and are resolved from the store by the engine;
* ``InPath`` / ``OutPath`` parameters are sandboxed file paths;
* everything else is passed through as JSON (``Literal`` becomes an enum).

The first line of the docstring is the short summary, the rest is the tool
description sent to the model.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from .types import HANDLE_KINDS, _PathRole


@dataclass
class Param:
    name: str
    annotation: Any  # resolved annotation (with Annotated extras)
    default: Any  # inspect.Parameter.empty if required
    handle_kind: str | None = None  # "image" / "contours" if this is a handle
    handle_list: bool = False  # list of handles
    optional: bool = False  # handle may be None
    path_role: _PathRole | None = None


@dataclass
class OpSpec:
    name: str
    module: str
    func: Callable[..., Any]
    params: list[Param]
    description: str

    @property
    def handle_params(self) -> list[Param]:
        return [p for p in self.params if p.handle_kind]


REGISTRY: dict[str, OpSpec] = {}


def _handle_kind(tp: Any) -> tuple[str | None, bool, bool]:
    """Return (kind, is_list, is_optional) for a parameter annotation."""
    optional = False
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        optional = len(args) != len(get_args(tp))
        if len(args) == 1:
            tp = args[0]
            origin = get_origin(tp)
    if tp in HANDLE_KINDS:
        return HANDLE_KINDS[tp], False, optional
    if origin is list and get_args(tp) and get_args(tp)[0] in HANDLE_KINDS:
        return HANDLE_KINDS[get_args(tp)[0]], True, optional
    return None, False, optional


def _path_role(tp: Any) -> _PathRole | None:
    # unwrap Optional[...] (InPath | None) so optional paths are sandboxed too
    if get_origin(tp) in (typing.Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            tp = args[0]
    if get_origin(tp) is Annotated:
        for extra in tp.__metadata__:
            if isinstance(extra, _PathRole):
                return extra
    return None


def op(module: str, name: str | None = None) -> Callable[[Callable], Callable]:
    def deco(func: Callable) -> Callable:
        hints = get_type_hints(func, include_extras=True)
        params = []
        for p in inspect.signature(func).parameters.values():
            ann = hints.get(p.name, Any)
            kind, is_list, optional = _handle_kind(ann)
            role = _path_role(ann)
            if role is not None:
                optional = type(None) in get_args(ann)
            params.append(
                Param(
                    name=p.name,
                    annotation=ann,
                    default=p.default,
                    handle_kind=kind,
                    handle_list=is_list,
                    optional=optional,
                    path_role=role,
                )
            )
        op_name = name or func.__name__
        if op_name in REGISTRY:
            raise ValueError(f"Duplicate op name {op_name}")
        REGISTRY[op_name] = OpSpec(
            name=op_name,
            module=module,
            func=func,
            params=params,
            description=inspect.cleandoc(func.__doc__ or op_name),
        )
        return func

    return deco
