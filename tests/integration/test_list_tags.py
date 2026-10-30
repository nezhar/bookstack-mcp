"""list_tags against a real BookStack."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.list_tags import list_tags

from .conftest import BookStackInstance, api

pytestmark = pytest.mark.integration


@pytest.fixture
async def server(bookstack: BookStackInstance) -> AsyncIterator[FastMCP[Any]]:
    creds = bookstack.viewer
    async with BookStackClient(bookstack.base_url, creds.token_id, creds.token_secret) as client:
        settings = Settings(
            url=bookstack.base_url,
            token_id=creds.token_id,
            token_secret=creds.token_secret,
            _env_file=None,
        )
        yield build_server(settings, client, await client.system(), tools=[list_tags])


async def test_lists_names_and_values(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    prefix = f"it{uuid.uuid4().hex[:8]}"
    status, owner = f"{prefix}-status", f"{prefix}-owner"
    tag_sets = [
        [{"name": status, "value": "draft"}, {"name": owner, "value": "ops"}],
        [{"name": status, "value": "draft"}],
        [{"name": status, "value": "final"}],
    ]
    for i, tags in enumerate(tag_sets):
        response = api(
            admin_client,
            "POST",
            "/pages",
            json={"book_id": sandbox_book, "name": f"Tagged {i}", "markdown": "x", "tags": tags},
        )
        assert response.status_code == 200, response.text

    async with Client(server) as mcp:
        names = (await mcp.call_tool("list_tags", {"name_contains": prefix})).structured_content
        values = (await mcp.call_tool("list_tags", {"name": status})).structured_content

    assert names is not None
    by_name = {row["name"]: row for row in names["names"]}
    assert set(by_name) == {status, owner}
    assert names["total"] == 2
    assert [row["name"] for row in names["names"]] == [status, owner]  # most used first
    assert by_name[status]["values"] == 2
    assert by_name[status]["usages"] == 3
    assert by_name[status]["page_count"] == 3
    assert by_name[owner]["usages"] == 1
    assert by_name[owner]["book_count"] == 0

    assert values is not None
    assert [(row["value"], row["usages"]) for row in values["values"]] == [
        ("draft", 2),
        ("final", 1),
    ]
    assert values["total"] == 2
