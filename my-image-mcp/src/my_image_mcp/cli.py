"""Command line entry point: ``my-image-mcp [serve|run|tools] ...``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--root",
        action="append",
        dest="roots",
        metavar="DIR",
        help="Directory the server may read/write (repeatable; default: current dir or $MY_IMAGE_MCP_ROOTS)",
    )
    p.add_argument("--allow-any-path", action="store_true", default=None, help="Disable the root directory sandbox")
    p.add_argument("--max-mb", type=int, dest="max_store_mb", help="In-memory store limit in MB (default 2048)")


def _settings(args: argparse.Namespace):
    from .config import Settings

    return Settings.from_env(
        roots=[Path(r) for r in args.roots] if args.roots else None,
        allow_any_path=args.allow_any_path,
        max_store_mb=args.max_store_mb,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my-image-mcp", description="Headless OpenCV MCP server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the MCP server (default)")
    _common(serve)
    serve.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--log-level", default="WARNING")

    run = sub.add_parser("run", help="Run an exported JSON pipeline and print the results")
    _common(run)
    run.add_argument("pipeline", help="Pipeline JSON file")

    sub.add_parser("tools", help="List the tools the server exposes")

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["serve", *argv]
    args = parser.parse_args(argv)

    if args.command == "tools":
        import anyio

        from .server import build_server

        async def _list():
            for t in await build_server().list_tools():
                print(f"{t.name:24} {(t.description or '').splitlines()[0]}")

        anyio.run(_list)
        return 0

    if args.command == "run":
        from .core.engine import Engine, dumps

        engine = Engine(_settings(args))
        data = json.loads(Path(args.pipeline).read_text(encoding="utf-8"))
        result = engine.run_pipeline(data["steps"] if isinstance(data, dict) else data)
        print(dumps(result))
        return 0 if result["ok"] else 1

    # serve (stdout belongs to the MCP protocol on stdio: log to stderr only)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr)
    from .server import build_server

    server = build_server(_settings(args))
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
