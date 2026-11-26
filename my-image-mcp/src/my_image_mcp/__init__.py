"""my-image-mcp: a headless MCP server exposing OpenCV image operations."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("my-image-mcp")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0+unknown"
