"""MCP server for BookStack."""

import importlib.metadata

__version__ = importlib.metadata.version("bookstack-mcp")

__all__ = ["__version__"]
