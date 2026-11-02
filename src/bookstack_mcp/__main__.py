"""Command-line entry point for bookstack-mcp.

- `bookstack-mcp` runs the MCP server over stdio.
- `bookstack-mcp check` tests the configuration against BookStack and exits.
- `bookstack-mcp --version` prints the version.

stdout carries MCP messages only: everything else, help and errors included, goes to stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import NoReturn

from bookstack_mcp import __version__
from bookstack_mcp.client import BookStackClient, BookStackError, RetryPolicy, SystemInfo
from bookstack_mcp.client.errors import (
    BookStackRateLimited,
    BookStackServerError,
    BookStackUnavailable,
)
from bookstack_mcp.config import ConfigError, Settings
from bookstack_mcp.logs import setup_logging
from bookstack_mcp.server import OLDEST_SUPPORTED_VERSION, SUPPORTED_VERSIONS, build_server

logger = logging.getLogger("bookstack_mcp")

SYSTEM_ATTEMPTS = 3
SYSTEM_RETRY_DELAY_S = 2.0
SYSTEM_TIMEOUT_S = 10.0
"""Per-attempt timeout at startup, so a hanging BookStack cannot stall the MCP handshake."""
_NO_RETRY = RetryPolicy(max_attempts=1)
"""The startup probe retries on its own schedule, not the client's."""
_RETRYABLE = (BookStackUnavailable, BookStackServerError, BookStackRateLimited)


class _StderrParser(argparse.ArgumentParser):
    """argparse writes help to stdout; send everything to stderr."""

    def _print_message(self, message: str, file: object = None) -> None:
        if message:
            sys.stderr.write(message)


def _parser() -> argparse.ArgumentParser:
    parser = _StderrParser(prog="bookstack-mcp", description="MCP server for BookStack.")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("check", help="test the configuration against BookStack and exit")
    return parser


def main(argv: list[str] | None = None) -> NoReturn:
    args = _parser().parse_args(argv)
    if args.version:
        print(f"bookstack-mcp {__version__}", file=sys.stderr)
        sys.exit(0)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"bookstack-mcp: {exc}", file=sys.stderr)
        sys.exit(2)
    if args.command == "check":
        sys.exit(asyncio.run(check(settings)))
    setup_logging(settings.log_level)
    for notice in settings.notices:
        logger.info(notice)
    asyncio.run(serve(settings))
    sys.exit(0)


def make_client(
    settings: Settings, *, timeout: float | None = None, retry: RetryPolicy | None = None
) -> BookStackClient:
    return BookStackClient(
        settings.url,
        settings.token_id.get_secret_value(),
        settings.token_secret,
        timeout=settings.timeout_seconds if timeout is None else timeout,
        verify=settings.ca_bundle or settings.verify_tls,
        requests_per_minute=settings.requests_per_minute,
        retry=retry,
    )


async def read_system(client: BookStackClient, *, attempts: int, delay_s: float) -> SystemInfo:
    """`/api/system`, retrying outages and server errors. Raises the last `BookStackError`."""
    for attempt in range(1, attempts + 1):
        try:
            return await client.system()
        except _RETRYABLE:
            if attempt == attempts:
                raise
            await asyncio.sleep(delay_s)
    raise AssertionError("unreachable")


def fallback_system(settings: Settings) -> SystemInfo:
    """What the server assumes when BookStack cannot be read at startup."""
    return SystemInfo(
        version=OLDEST_SUPPORTED_VERSION,
        instance_id="",
        app_name="BookStack",
        base_url=settings.url,
    )


async def startup_system(settings: Settings, client: BookStackClient) -> SystemInfo:
    """Read `/api/system`, falling back to the oldest supported version with a warning."""
    try:
        system = await read_system(client, attempts=SYSTEM_ATTEMPTS, delay_s=SYSTEM_RETRY_DELAY_S)
    except BookStackError as exc:
        logger.warning(
            "could not read /api/system; starting anyway and assuming the oldest supported "
            f"BookStack version {OLDEST_SUPPORTED_VERSION}. {exc}",
            extra={"attempts": SYSTEM_ATTEMPTS},
        )
        return fallback_system(settings)
    try:
        version = system.version_tuple
    except ValueError:
        logger.warning(
            "BookStack reported a version that is not a version number; assuming "
            f"{OLDEST_SUPPORTED_VERSION}"
        )
        return fallback_system(settings).model_copy(update={"base_url": system.base_url})
    if version < (26, 5, 0):
        logger.warning(
            f"BookStack {system.version} is older than the supported versions "
            f"({SUPPORTED_VERSIONS}); some tools may fail",
            extra={"version": system.version},
        )
    return system


async def serve(settings: Settings) -> None:
    timeout = min(settings.timeout_seconds, SYSTEM_TIMEOUT_S)
    async with make_client(settings, timeout=timeout, retry=_NO_RETRY) as probe:
        system = await startup_system(settings, probe)
    async with make_client(settings) as client:
        mcp = build_server(settings, client, system)
        logger.info(
            "starting the MCP server over stdio",
            extra={
                "version": system.version,
                "toolsets": sorted(settings.toolsets),
                "read_only": settings.read_only,
                "allow_delete": settings.allow_delete,
            },
        )
        await mcp.run_stdio_async(show_banner=False)


async def check(settings: Settings) -> int:
    """Test the configuration and print a report to stderr. Returns the exit code."""

    def out(line: str) -> None:
        print(line, file=sys.stderr)

    async with make_client(settings) as client:
        try:
            system = await client.system()
            await client.list_page("/books", count=1)
        except BookStackError as exc:
            out(f"BookStack URL: {settings.url}")
            out(f"Check failed: {exc.message}")
            out(f"Hint: {exc.hint}")
            return 1
        mcp = build_server(settings, client, system)
        tools = sorted(t.name for t in await mcp.list_tools())
    out(f"BookStack version: {system.version}")
    out(f"BookStack URL: {settings.url}")
    out(f"Toolsets: {', '.join(sorted(settings.toolsets))}")
    out(f"Read-only: {'yes' if settings.read_only else 'no'}")
    out(f"Tools ({len(tools)}): {', '.join(tools) or 'none'}")
    if system.version_tuple < (26, 5, 0):
        out(f"Warning: supported BookStack versions are {SUPPORTED_VERSIONS}")
    return 0


if __name__ == "__main__":
    main()
