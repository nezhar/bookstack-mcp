"""The browse tool against a real BookStack."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.browse import browse

from .conftest import BookStackInstance, _expect, api

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Library:
    tag: str
    shelf_id: int
    book_id: int
    chapter_id: int
    chapter_pages: tuple[int, int]
    loose_page: int


@pytest.fixture
def library(admin_client: httpx2.Client) -> Iterator[Library]:
    """A shelf with one book: a chapter with two pages, then one page outside the chapter."""
    tag = uuid.uuid4().hex
    book = _expect(api(admin_client, "POST", "/books", json={"name": f"it-browse-{tag}"}), 200)
    shelf_id = 0
    try:
        chapter = _expect(
            api(admin_client, "POST", "/chapters", json={"book_id": book["id"], "name": "Ch"}),
            200,
        )
        pages = [
            _expect(
                api(
                    admin_client,
                    "POST",
                    "/pages",
                    json={"chapter_id": chapter["id"], "name": name, "markdown": "# x"},
                ),
                200,
            )
            for name in ("First", "Second")
        ]
        loose = _expect(
            api(
                admin_client,
                "POST",
                "/pages",
                json={"book_id": book["id"], "name": "Loose", "markdown": "# y"},
            ),
            200,
        )
        shelf = _expect(
            api(
                admin_client,
                "POST",
                "/shelves",
                json={"name": f"it-browse-shelf-{tag}", "books": [book["id"]]},
            ),
            200,
        )
        shelf_id = int(shelf["id"])
        yield Library(
            tag=tag,
            shelf_id=shelf_id,
            book_id=int(book["id"]),
            chapter_id=int(chapter["id"]),
            chapter_pages=(int(pages[0]["id"]), int(pages[1]["id"])),
            loose_page=int(loose["id"]),
        )
    finally:
        if shelf_id:
            api(admin_client, "DELETE", f"/shelves/{shelf_id}")
        api(admin_client, "DELETE", f"/books/{book['id']}")


@pytest.fixture
async def mcp(bookstack: BookStackInstance) -> AsyncIterator[Client[Any]]:
    admin = bookstack.admin
    settings = Settings(
        url=bookstack.base_url,
        token_id=admin.token_id,
        token_secret=admin.token_secret,
        _env_file=None,
    )
    async with BookStackClient(bookstack.base_url, admin.token_id, admin.token_secret) as client:
        system = await client.system()
        async with Client(build_server(settings, client, system, tools=[browse])) as mcp:
            yield mcp


async def _browse(mcp: Client[Any], **args: Any) -> dict[str, Any]:
    result = await mcp.call_tool("browse", args)
    assert result.structured_content is not None
    return result.structured_content


async def test_library_lists_shelf_and_book(mcp: Client[Any], library: Library) -> None:
    data = await _browse(mcp, name_contains=library.tag)
    assert [s["id"] for s in data["shelves"]["items"]] == [library.shelf_id]
    assert [b["id"] for b in data["books"]["items"]] == [library.book_id]
    assert data["shelves"]["total"] == 1
    assert data["books"]["total"] == 1
    assert data["books"]["items"][0]["updated_at"]


async def test_name_contains_finds_book_by_fragment(mcp: Client[Any], library: Library) -> None:
    data = await _browse(mcp, name_contains=f"browse-{library.tag[4:20]}")
    assert library.book_id in [b["id"] for b in data["books"]["items"]]


async def test_name_contains_escapes_wildcards(mcp: Client[Any], library: Library) -> None:
    data = await _browse(mcp, name_contains=f"browse_{library.tag}")
    assert data["books"]["items"] == []


async def test_shelf_lists_its_book(mcp: Client[Any], library: Library) -> None:
    data = await _browse(mcp, shelf_id=library.shelf_id)
    assert [b["id"] for b in data["books"]["items"]] == [library.book_id]


async def test_book_returns_tree_in_order(mcp: Client[Any], library: Library) -> None:
    data = await _browse(mcp, book_id=library.book_id)
    [chapter, loose] = data["contents"]
    assert (chapter["type"], chapter["id"]) == ("chapter", library.chapter_id)
    assert tuple(p["id"] for p in chapter["pages"]) == library.chapter_pages
    assert (loose["type"], loose["id"]) == ("page", library.loose_page)
    assert "html" not in str(data) and "markdown" not in str(data)


async def test_chapter_returns_both_pages(mcp: Client[Any], library: Library) -> None:
    data = await _browse(mcp, chapter_id=library.chapter_id)
    assert tuple(p["id"] for p in data["pages"]) == library.chapter_pages
    assert data["book_id"] == library.book_id
    assert all(p["url"].endswith(f"/link/{p['id']}") for p in data["pages"])


async def test_missing_book_is_not_found(mcp: Client[Any]) -> None:
    with pytest.raises(ToolError, match="404"):
        await mcp.call_tool("browse", {"book_id": 999999999})
