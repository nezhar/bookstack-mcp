"""recent_changes against a real BookStack."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.recent_changes import recent_changes

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
        yield build_server(settings, client, await client.system(), tools=[recent_changes])


def _create(admin: httpx2.Client, book_id: int, name: str) -> dict[str, Any]:
    response = api(
        admin, "POST", "/pages", json={"book_id": book_id, "name": name, "markdown": "v1"}
    )
    assert response.status_code == 200, response.text
    page: dict[str, Any] = response.json()
    return page


def _update(admin: httpx2.Client, page_id: int) -> None:
    response = api(admin, "PUT", f"/pages/{page_id}", json={"markdown": "v2"})
    assert response.status_code == 200, response.text


async def test_since_splits_updates(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page_a = _create(admin_client, sandbox_book, "Page A")
    page_b = _create(admin_client, sandbox_book, "Page B")

    _update(admin_client, page_a["id"])
    await asyncio.sleep(2)
    noted = datetime.now(UTC).replace(microsecond=0)
    await asyncio.sleep(1)
    _update(admin_client, page_b["id"])

    async with Client(server) as mcp:
        after = await mcp.call_tool(
            "recent_changes", {"since": noted.isoformat(), "book_id": sandbox_book}
        )
        everything = await mcp.call_tool("recent_changes", {"book_id": sandbox_book})

    content = after.structured_content
    assert content is not None
    assert [p["page_id"] for p in content["pages"]] == [page_b["id"]]
    assert content["total"] == 1
    [row] = content["pages"]
    assert row["book_id"] == sandbox_book
    assert row["chapter_id"] is None
    assert row["url"].endswith(f"/link/{page_b['id']}")
    assert isinstance(row["updated_by"], int)

    full = everything.structured_content
    assert full is not None
    assert [p["page_id"] for p in full["pages"]] == [page_b["id"], page_a["id"]]  # newest first
