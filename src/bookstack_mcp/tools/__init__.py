"""MCP tools, one module per toolset.

Each public module here (no leading underscore) declares tools at module level with
`@tool(...)`; `discover_tools()` collects them. See "Writing a tool" in AGENTS.md.
"""

from __future__ import annotations

import importlib
import pkgutil

from bookstack_mcp.tools._base import (
    FEATURE_NAMES,
    CurrentDeps,
    Deps,
    ToolSpec,
    get_deps,
    to_tool_error,
    tool,
    tool_call,
)

__all__ = [
    "FEATURE_NAMES",
    "CurrentDeps",
    "Deps",
    "ToolSpec",
    "discover_tools",
    "get_deps",
    "to_tool_error",
    "tool",
    "tool_call",
]


def discover_tools() -> list[ToolSpec]:
    """Every module-level `ToolSpec` in this package's public modules, sorted by name."""
    found: dict[str, ToolSpec] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for value in vars(module).values():
            if not isinstance(value, ToolSpec):
                continue
            other = found.setdefault(value.name, value)
            if other is not value:
                raise ValueError(f"two tools are named {value.name!r}")
    return [found[name] for name in sorted(found)]
