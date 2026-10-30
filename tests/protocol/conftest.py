"""Shared pieces for protocol tests: settings, a fake BookStack and server factories.

Protocol tests drive the server through FastMCP's in-memory `Client`. The BookStack client is
the real `BookStackClient` on an `httpx2.MockTransport`, so tools run their real request path
against canned responses. Use these only to test MCP behaviour and error mapping; claims about
BookStack's API belong in integration tests.

Example:

    async def test_get_page(make_server, fake_bookstack):
        fake_bookstack.add("GET", "/api/pages/1", {"id": 1, "name": "Intro"})
        async with Client(make_server(tools=[get_page])) as mcp:
            result = await mcp.call_tool("get_page", {"page_id": 1})
        assert result.structured_content["name"] == "Intro"
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest
from fastmcp import FastMCP

from bookstack_mcp.client import BookStackClient, SystemInfo
from bookstack_mcp.config import Settings
from bookstack_mcp.logs import setup_logging
from bookstack_mcp.server import build_server
from bookstack_mcp.tools import ToolSpec

BASE_URL = "https://docs.example.com"


def system_info(version: str = "v26.09.2") -> SystemInfo:
    return SystemInfo(version=version, instance_id="test", app_name="BookStack", base_url=BASE_URL)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"url": BASE_URL, "token_id": "id", "token_secret": "secret"}
    values.update(overrides)
    return Settings(**values, _env_file=None)


@dataclass
class FakeBookStack:
    """Canned responses by `(method, path)`; unknown routes answer 404 like BookStack."""

    routes: dict[tuple[str, str], tuple[int, Any]] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def add(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.routes[(method.upper(), path)] = (status, body)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.routes.get(
            (request.method, request.url.path),
            (404, {"error": {"code": 404, "message": "Not found"}}),
        )
        return httpx2.Response(status, json=body)


@pytest.fixture
def fake_bookstack() -> FakeBookStack:
    return FakeBookStack()


@pytest.fixture
async def bookstack_client(fake_bookstack: FakeBookStack) -> AsyncIterator[BookStackClient]:
    client = BookStackClient(
        BASE_URL, "id", "secret", transport=httpx2.MockTransport(fake_bookstack.handler)
    )
    yield client
    await client.aclose()


MakeServer = Callable[..., FastMCP[Any]]


@pytest.fixture
def make_server(bookstack_client: BookStackClient) -> MakeServer:
    """`make_server(tools=[...], version="v26.05.5", read_only=True, ...)`.

    Keyword arguments other than `tools` and `version` are settings fields.
    """

    def factory(
        *, tools: Sequence[ToolSpec], version: str = "v26.09.2", **settings: Any
    ) -> FastMCP[Any]:
        return build_server(
            make_settings(**settings), bookstack_client, system_info(version), tools=tools
        )

    return factory


@pytest.fixture
def json_logs() -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """Set up the JSON logging at DEBUG; call the result to get the records written so far."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    setup_logging("DEBUG")
    stream = io.StringIO()
    [handler] = [h for h in root.handlers if type(h) is logging.StreamHandler]
    handler.setStream(stream)

    def read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    yield read
    root.handlers[:] = handlers
    root.setLevel(level)
