"""update_page against a real BookStack: rename, content, guards, tags and moves."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.update_page import update_page

from .conftest import BookStackInstance, _expect, api

pytestmark = pytest.mark.integration


@pytest.fixture
async def server(bookstack: BookStackInstance) -> AsyncIterator[FastMCP[Any]]:
    creds = bookstack.admin
    settings = Settings(
        url=bookstack.base_url,
        token_id=creds.token_id,
        token_secret=creds.token_secret,
        _env_file=None,
    )
    async with BookStackClient(bookstack.base_url, creds.token_id, creds.token_secret) as client:
        system = await client.system()
        yield build_server(settings, client, system, tools=[update_page])


@pytest.fixture
def other_book(admin_client: httpx2.Client) -> Iterator[int]:
    """A second book to move pages into, deleted after the test."""
    body = {"name": f"it-{uuid.uuid4().hex}"}
    book = _expect(api(admin_client, "POST", "/books", json=body), 200)
    yield int(book["id"])
    api(admin_client, "DELETE", f"/books/{book['id']}")


def create_page(admin_client: httpx2.Client, book_id: int, **fields: Any) -> dict[str, Any]:
    body = {"book_id": book_id, "name": "Update target", **fields}
    page: dict[str, Any] = _expect(api(admin_client, "POST", "/pages", json=body), 200)
    return page


def read_page(admin_client: httpx2.Client, page_id: int) -> dict[str, Any]:
    page: dict[str, Any] = _expect(api(admin_client, "GET", f"/pages/{page_id}"), 200)
    return page


async def update(server: FastMCP[Any], **args: Any) -> Any:
    async with Client(server) as mcp:
        return await mcp.call_tool("update_page", args, raise_on_error=False)


async def test_rename(server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int) -> None:
    page = create_page(admin_client, sandbox_book, markdown="Body text.")

    result = await update(server, page_id=page["id"], name="Renamed page")

    assert not result.is_error, result.content
    assert result.structured_content["verified"] is True
    stored = read_page(admin_client, page["id"])
    assert stored["name"] == "Renamed page"
    assert stored["markdown"] == "Body text."
    assert result.structured_content["updated_at"] == stored["updated_at"]


async def test_replace_markdown_content(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="# Old\n\nOld body text.")
    before = read_page(admin_client, page["id"])

    result = await update(
        server,
        page_id=page["id"],
        markdown="# New\n\nA new body text.",
        expected_updated_at=before["updated_at"],
    )

    assert not result.is_error, result.content
    data = result.structured_content
    assert data["verified"] is True
    assert data["content"]["source_field"] == "markdown"
    stored = read_page(admin_client, page["id"])
    assert stored["markdown"] == "# New\n\nA new body text."
    assert stored["revision_count"] == before["revision_count"] + 1
    assert data["revision_count"] == stored["revision_count"]


async def test_replacement_under_half_the_size_needs_allow_shrink(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    original = " ".join(["Long paragraph."] * 20)
    page = create_page(admin_client, sandbox_book, markdown=original)

    result = await update(server, page_id=page["id"], markdown="Short.")

    assert result.is_error
    assert "allow_shrink" in result.content[0].text
    assert read_page(admin_client, page["id"])["markdown"] == original

    result = await update(server, page_id=page["id"], markdown="Short.", allow_shrink=True)
    assert not result.is_error, result.content
    assert read_page(admin_client, page["id"])["markdown"] == "Short."


async def test_tags_are_replaced(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    tags = [{"name": "status", "value": "draft"}, {"name": "team", "value": "docs"}]
    page = create_page(admin_client, sandbox_book, markdown="Text.", tags=tags)

    result = await update(
        server, page_id=page["id"], tags=[{"name": "status", "value": "final"}, {"name": "x"}]
    )

    assert not result.is_error, result.content
    assert result.structured_content["verified"] is True
    stored = {(t["name"], t["value"]) for t in read_page(admin_client, page["id"])["tags"]}
    assert stored == {("status", "final"), ("x", "")}
    returned = {(t["name"], t["value"]) for t in result.structured_content["tags"]}
    assert returned == stored


async def test_move_between_books_and_into_a_chapter(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int, other_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="Moving page.")

    result = await update(server, page_id=page["id"], book_id=other_book)

    assert not result.is_error, result.content
    assert result.structured_content["location"] == {"book_id": other_book, "chapter_id": None}
    stored = read_page(admin_client, page["id"])
    assert stored["book_id"] == other_book
    assert not stored["chapter_id"]

    chapter = _expect(
        api(admin_client, "POST", "/chapters", json={"book_id": sandbox_book, "name": "Ch"}), 200
    )
    result = await update(server, page_id=page["id"], chapter_id=chapter["id"])

    assert not result.is_error, result.content
    assert result.structured_content["verified"] is True
    assert result.structured_content["location"] == {
        "book_id": sandbox_book,
        "chapter_id": chapter["id"],
    }
    stored = read_page(admin_client, page["id"])
    assert (stored["book_id"], stored["chapter_id"]) == (sandbox_book, chapter["id"])
    assert stored["markdown"] == "Moving page."


async def test_format_change_is_refused_without_the_flag(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="Markdown text.")
    before = read_page(admin_client, page["id"])

    result = await update(server, page_id=page["id"], html="<p>HTML text.</p>")

    assert result.is_error
    assert "allow_format_change" in result.content[0].text
    after = read_page(admin_client, page["id"])
    assert after["markdown"] == "Markdown text."
    assert after["updated_at"] == before["updated_at"]

    result = await update(
        server, page_id=page["id"], html="<p>HTML text, longer.</p>", allow_format_change=True
    )
    assert not result.is_error, result.content
    assert result.structured_content["verified"] is True
    stored = read_page(admin_client, page["id"])
    assert stored["markdown"] == ""
    assert "HTML text, longer." in stored["raw_html"]
