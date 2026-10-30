"""create_page against a real BookStack, through the MCP protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.client import BookStackClient, parse_version
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.create_page import create_page

from .conftest import BookStackInstance, Credentials, api

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _mcp(bookstack: BookStackInstance, credentials: Credentials) -> AsyncIterator[Client]:
    async with BookStackClient(
        bookstack.base_url, credentials.token_id, credentials.token_secret
    ) as client:
        settings = Settings(
            url=bookstack.base_url,
            token_id=credentials.token_id,
            token_secret=credentials.token_secret,
            _env_file=None,
        )
        server = build_server(settings, client, await client.system(), tools=[create_page])
        async with Client(server) as mcp:
            yield mcp


@pytest.fixture
async def admin_mcp(bookstack: BookStackInstance) -> AsyncIterator[Client]:
    async with _mcp(bookstack, bookstack.admin) as mcp:
        yield mcp


async def _create(mcp: Client, **args: Any) -> dict[str, Any]:
    result = await mcp.call_tool("create_page", args)
    content: dict[str, Any] = result.structured_content
    return content


def _read(admin_client: httpx2.Client, page_id: int) -> dict[str, Any]:
    response = api(admin_client, "GET", f"/pages/{page_id}")
    assert response.status_code == 200, response.text
    page: dict[str, Any] = response.json()
    return page


async def test_markdown_page_in_book(
    admin_mcp: Client, admin_client: httpx2.Client, sandbox_book: int
) -> None:
    created = await _create(admin_mcp, name="Markdown page", book_id=sandbox_book, markdown="# Hi")
    assert created["source_field"] == "markdown"
    assert created["book_id"] == sandbox_book
    assert created["chapter_id"] is None
    assert created["url"].endswith(f"/link/{created['page_id']}")

    page = _read(admin_client, created["page_id"])
    assert page["markdown"] == "# Hi"
    assert page["editor"] == created["editor"]
    assert page["updated_at"] == created["updated_at"]


async def test_html_page_in_chapter(
    admin_mcp: Client, admin_client: httpx2.Client, sandbox_book: int
) -> None:
    chapter = api(admin_client, "POST", "/chapters", json={"book_id": sandbox_book, "name": "Ch"})
    assert chapter.status_code == 200, chapter.text
    chapter_id = chapter.json()["id"]

    created = await _create(
        admin_mcp, name="HTML page", chapter_id=chapter_id, html="<p>Hello there</p>"
    )
    assert created["source_field"] == "html"
    assert created["chapter_id"] == chapter_id
    assert created["book_id"] == sandbox_book

    page = _read(admin_client, created["page_id"])
    assert page["markdown"] == ""
    assert "Hello there" in page["raw_html"]


async def test_page_from_template_copies_stored_content(
    admin_mcp: Client, admin_client: httpx2.Client, sandbox_book: int
) -> None:
    template = await _create(
        admin_mcp, name="Template", book_id=sandbox_book, markdown="## Steps\n\n1. One"
    )
    created = await _create(
        admin_mcp, name="From template", book_id=sandbox_book, template_page_id=template["page_id"]
    )
    assert created["source_field"] == "markdown"
    assert _read(admin_client, created["page_id"])["markdown"] == "## Steps\n\n1. One"


async def test_tags_round_trip(
    admin_mcp: Client, admin_client: httpx2.Client, sandbox_book: int
) -> None:
    tags = [{"name": "team", "value": "ops"}, {"name": "reviewed", "value": ""}]
    created = await _create(admin_mcp, name="Tagged", book_id=sandbox_book, markdown="x", tags=tags)
    page = _read(admin_client, created["page_id"])
    assert sorted((t["name"], t["value"]) for t in page["tags"]) == [
        ("reviewed", ""),
        ("team", "ops"),
    ]


async def test_changelog_is_the_revision_summary(
    admin_mcp: Client,
    admin_client: httpx2.Client,
    sandbox_book: int,
    tinker: Callable[[str], str],
) -> None:
    version = api(admin_client, "GET", "/system").json()["version"]
    if parse_version(version) < (26, 9, 0):
        pytest.skip("revision notes need BookStack 26.09")
    created = await _create(
        admin_mcp, name="Noted", book_id=sandbox_book, markdown="x", changelog="First draft"
    )
    output = tinker(
        "echo \\BookStack\\Entities\\Models\\PageRevision::query()"
        f'->where("page_id", {created["page_id"]})->where("type", "version")'
        '->orderByDesc("id")->value("summary");'
    )
    assert output.strip().splitlines()[-1] == "First draft"


async def test_viewer_gets_a_permission_error(
    bookstack: BookStackInstance, sandbox_book: int
) -> None:
    async with _mcp(bookstack, bookstack.viewer) as mcp:
        result = await mcp.call_tool(
            "create_page",
            {"name": "Nope", "book_id": sandbox_book, "markdown": "x"},
            raise_on_error=False,
        )
    assert result.is_error
    assert "HTTP 403" in result.content[0].text


async def test_blank_name_returns_bookstack_field_error(
    admin_mcp: Client, sandbox_book: int
) -> None:
    # The schema requires a non-empty name; BookStack trims it and reports the field error.
    result = await admin_mcp.call_tool(
        "create_page",
        {"name": "   ", "book_id": sandbox_book, "markdown": "x"},
        raise_on_error=False,
    )
    assert result.is_error
    assert "Field errors: name:" in result.content[0].text
