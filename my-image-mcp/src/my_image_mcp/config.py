"""Server settings and filesystem sandboxing.

Every path a tool reads or writes goes through :meth:`Settings.resolve_path`,
which pins it inside one of the configured root directories (unless the
server was started with ``--allow-any-path``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_ROOTS = "MY_IMAGE_MCP_ROOTS"
ENV_MAX_MB = "MY_IMAGE_MCP_MAX_MB"
ENV_ANY_PATH = "MY_IMAGE_MCP_ALLOW_ANY_PATH"


class PathNotAllowed(PermissionError):
    pass


@dataclass
class Settings:
    roots: list[Path] = field(default_factory=lambda: [Path.cwd()])
    allow_any_path: bool = False
    max_store_mb: int = 2048
    preview_max_size: int = 1024

    def __post_init__(self) -> None:
        self.roots = [Path(r).expanduser().resolve() for r in self.roots]
        if not self.roots:
            self.roots = [Path.cwd().resolve()]

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        kwargs: dict = {}
        if os.environ.get(ENV_ROOTS):
            kwargs["roots"] = [Path(p) for p in os.environ[ENV_ROOTS].split(os.pathsep) if p]
        if os.environ.get(ENV_MAX_MB):
            kwargs["max_store_mb"] = int(os.environ[ENV_MAX_MB])
        if os.environ.get(ENV_ANY_PATH, "").lower() in ("1", "true", "yes"):
            kwargs["allow_any_path"] = True
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kwargs)

    def resolve_path(self, path: str, *, write: bool = False) -> Path:
        """Resolve ``path`` (relative paths are taken from the first root) and
        check that it stays inside an allowed root."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.roots[0] / p
        p = p.resolve()
        if not self.allow_any_path and not any(p.is_relative_to(r) for r in self.roots):
            roots = ", ".join(str(r) for r in self.roots)
            raise PathNotAllowed(f"Path {p} is outside the allowed roots ({roots}).")
        if write:
            p.parent.mkdir(parents=True, exist_ok=True)
        elif not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        return p
