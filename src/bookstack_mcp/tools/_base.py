"""What every tool module uses: the `tool` decorator, `Deps` and the shared error mapping.

A tool module defines async functions decorated with `@tool(...)`. The decorator does not
touch FastMCP; it returns a `ToolSpec` holding the function and its metadata. `build_server`
finds every module-level `ToolSpec` in `bookstack_mcp.tools` and registers it, so a new
module needs no wiring. See "Writing a tool" in AGENTS.md for the full pattern.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from mcp_types import ToolAnnotations

from bookstack_mcp.client import BookStackClient, Features, Links, SystemInfo
from bookstack_mcp.client.errors import BookStackError, BookStackValidationError
from bookstack_mcp.config import Settings, Toolset
from bookstack_mcp.content.chunk import InvalidChunkRequest
from bookstack_mcp.editing.errors import EditingError
from bookstack_mcp.logs import request_scope

__all__ = [
    "FEATURE_NAMES",
    "CurrentDeps",
    "Deps",
    "ToolSpec",
    "get_deps",
    "to_tool_error",
    "tool",
    "tool_call",
]

logger = logging.getLogger("bookstack_mcp.tools")

FEATURE_NAMES: frozenset[str] = frozenset(f.name for f in dataclasses.fields(Features))
"""Valid values for `@tool(needs=...)`: the field names of `Features`."""

DEPS_KEY = "bookstack_mcp.deps"
"""Key of `Deps` in the server's lifespan context."""

ToolFn = Callable[..., Awaitable[Any]]


# -- dependencies ----------------------------------------------------------------------


@dataclass(frozen=True)
class Deps:
    """What a tool may use. Request it with a `deps: Deps = CurrentDeps()` parameter."""

    settings: Settings
    client: BookStackClient
    system: SystemInfo
    """`/api/system` as read at startup (or the fallback when BookStack was unreachable)."""

    @property
    def features(self) -> Features:
        return self.system.features

    @property
    def links(self) -> Links:
        """Links on the instance's own base URL (its APP_URL), as users open them."""
        return self.system.links


def get_deps() -> Deps:
    """The `Deps` of the running server, from its lifespan context."""
    deps = get_context().lifespan_context.get(DEPS_KEY)
    if not isinstance(deps, Deps):
        raise RuntimeError("the server was not built with build_server()")
    return deps


def CurrentDeps() -> Deps:
    """Parameter default that injects `Deps`. FastMCP hides it from the tool's schema."""
    return Depends(get_deps)


# -- errors and call logging -----------------------------------------------------------


def to_tool_error(exc: Exception) -> ToolError | None:
    """The `ToolError` for an expected failure, or `None` for an unexpected one.

    BookStack errors keep the status, BookStack's message, its field errors and the hint.
    Editing and chunk errors already say what is wrong and how to fix it.
    """
    if isinstance(exc, BookStackError):
        status = f" (HTTP {exc.status})" if exc.status is not None else ""
        parts = [f"BookStack error{status}: {exc.message}"]
        if isinstance(exc, BookStackValidationError) and exc.field_errors:
            fields = "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.field_errors.items())
            parts.append(f"Field errors: {fields}.")
        parts.append(f"Hint: {exc.hint}")
        return ToolError(" ".join(parts), log_level=logging.WARNING)
    if isinstance(exc, EditingError | InvalidChunkRequest):
        return ToolError(str(exc), log_level=logging.WARNING)
    return None


def tool_call(name: str, fn: ToolFn) -> ToolFn:
    """Wrap a tool function: map errors to `ToolError` and log one line per call.

    The line has `tool`, a random `request_id` (also set on every log line made during the
    call, such as the client's request lines), `duration_ms` and `outcome` (`ok` or the
    error class). Argument values are never logged. Unexpected exceptions are re-raised;
    FastMCP turns them into a masked error.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        request_id = secrets.token_hex(8)
        started = time.monotonic()
        outcome = "ok"
        with request_scope(request_id):
            try:
                return await fn(*args, **kwargs)
            except ToolError:
                outcome = "ToolError"
                raise
            except Exception as exc:
                outcome = type(exc).__name__
                mapped = to_tool_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc
            finally:
                logger.info(
                    "tool call",
                    extra={
                        "tool": name,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "outcome": outcome,
                    },
                )

    return wrapper


# -- the decorator ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """A tool function and the metadata `build_server` registers it with."""

    fn: ToolFn
    name: str
    toolset: Toolset
    write: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    needs: frozenset[str]
    title: str | None

    @property
    def tags(self) -> set[str]:
        """The toolset tag, plus `write` and `destructive` where they apply."""
        tags = {self.toolset.value}
        if self.write:
            tags.add("write")
        if self.destructive:
            tags.add("destructive")
        return tags

    @property
    def annotations(self) -> ToolAnnotations:
        return ToolAnnotations(
            title=self.title,
            read_only_hint=not self.write,
            destructive_hint=self.destructive,
            idempotent_hint=self.idempotent or not self.write,
            open_world_hint=self.open_world,
        )

    def missing_features(self, features: Features) -> frozenset[str]:
        """The features in `needs` that `features` lacks."""
        return frozenset(n for n in self.needs if not getattr(features, n))


def tool(
    toolset: Toolset,
    *,
    write: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
    needs: Iterable[str] = (),
    name: str | None = None,
    title: str | None = None,
) -> Callable[[ToolFn], ToolSpec]:
    """Declare a tool. The function's docstring is its description; its name is the tool name.

    - `toolset`: the one toolset the tool belongs to.
    - `write`: the tool changes something in BookStack (hidden in read-only mode).
    - `destructive`: the tool deletes something (implies `write`; hidden unless deletes are
      allowed).
    - `idempotent`: repeating the call with the same arguments has no further effect
      (read-only tools always are).
    - `open_world`: the tool reaches beyond BookStack, such as fetching a URL.
    - `needs`: `Features` field names the tool requires; on an older BookStack it is hidden.
    """
    needed = frozenset(needs)
    unknown = needed - FEATURE_NAMES
    if unknown:
        raise ValueError(f"unknown features in needs: {sorted(unknown)}")

    def decorate(fn: ToolFn) -> ToolSpec:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"tool {fn.__name__} must be an async function")
        if not (fn.__doc__ or "").strip():
            raise ValueError(f"tool {fn.__name__} needs a docstring; it is the description")
        return ToolSpec(
            fn=fn,
            name=name or fn.__name__,
            toolset=Toolset(toolset),
            write=write or destructive,
            destructive=destructive,
            idempotent=idempotent,
            open_world=open_world,
            needs=needed,
            title=title,
        )

    return decorate
