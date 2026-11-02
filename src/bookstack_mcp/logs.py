"""JSON logging to stderr. stdout is reserved for MCP messages in stdio mode.

Pass context as `extra`: `log.info("request done", extra={"method": "GET", "status": 200})`.
Only keys in `ALLOWED_FIELDS` keep their value; any other extra key is written as
`"<redacted>"`, so page content or secrets passed by mistake never reach the log.

`request_scope(request_id)` tags every record logged inside it (on any logger) with that
`request_id`, so the client's request lines can be matched to the tool call that made them.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "ALLOWED_FIELDS",
    "REDACTED",
    "JsonFormatter",
    "RequestIdFilter",
    "request_scope",
    "setup_logging",
]

ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "tool",
        "request_id",
        "method",
        "path",
        "status",
        "duration_ms",
        "attempt",
        "retry_in_ms",
        "page_id",
        "book_id",
        "chapter_id",
        "shelf_id",
        "count",
        "chars",
        "version",
        "toolsets",
        "read_only",
        "outcome",
        "tools",
        "ignored_arguments",
        "allow_delete",
        "attempts",
    }
)

REDACTED = "<redacted>"

# Attributes every LogRecord has; anything else on a record came from `extra`.
_RECORD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}

# Loggers of HTTP libraries that log full request URLs, query strings included.
_QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")

# FastMCP installs its own (rich) handler on this logger; we route it through ours instead.
_FASTMCP_LOGGER = "fastmcp"

_request_id: ContextVar[str | None] = ContextVar("bookstack_mcp_request_id", default=None)


@contextmanager
def request_scope(request_id: str) -> Iterator[None]:
    """Attach `request_id` to every record logged in this context (and tasks it starts)."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Add the current `request_scope` id to records that do not carry one."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = _request_id.get()
        if request_id is not None and not hasattr(record, "request_id"):
            record.request_id = request_id
        return True


class JsonFormatter(logging.Formatter):
    """Format a record as one JSON object: `ts`, `level`, `logger`, `msg`, then extras."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RECORD_ATTRS or key in entry:
                continue
            if key not in ALLOWED_FIELDS:
                entry[key] = REDACTED
            elif key == "path" and isinstance(value, str):
                entry[key] = _strip_query(value)
            else:
                entry[key] = value
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            # Type only: exception messages can carry URLs with query strings or page text.
            entry["exc_type"] = type(exc).__name__
        return json.dumps(entry, default=str, ensure_ascii=False)


def _strip_query(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def setup_logging(level: str | int = "INFO") -> None:
    """Give the root logger a single JSON handler on stderr at `level`.

    Safe to call again: it replaces the handlers it finds. FastMCP's own logger loses its
    rich handler and propagates here at WARNING and above.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    for old in root.handlers[:]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    fastmcp_logger = logging.getLogger(_FASTMCP_LOGGER)
    for old in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(old)
    fastmcp_logger.propagate = True
    fastmcp_logger.setLevel(logging.WARNING)
