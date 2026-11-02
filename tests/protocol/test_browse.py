"""The browse tool's MCP behaviour: arguments, request shape, result shape, error mapping."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

from bookstack_mcp.tools.browse import browse, escape_like

from .conftest import BASE_URL, FakeBookStack, MakeServer


async def _call(make_server: MakeServer, args: dict[str, Any]) -> Any:
    async with Client(make_server(tools=[browse])) as mcp:
        return await mcp.call_tool("browse", args, raise_on_error=False)


def test_escape_like() -> None:
    assert escape_like("50%_off\\x") == "50\\%\\_off\\\\x"


async def test_browse_is_core_and_read_only(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[browse], read_only=True)) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.name == "browse"
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is True
    assert listed.annotations.destructive_hint is False
    async with Client(make_server(tools=[browse], toolsets="structure")) as mcp:
        assert await mcp.list_tools() == []


async def test_library_lists_shelves_and_books(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/shelves",
        {"data": [{"id": 1, "name": "Ops", "slug": "ops", "updated_at": "t1"}], "total": 3},
    )
    fake_bookstack.add(
        "GET",
        "/api/books",
        {"data": [{"id": 7, "name": "Guide", "slug": "guide", "updated_at": "t2"}], "total": 1},
    )
    result = await _call(make_server, {"name_contains": "a%b", "offset": 2, "count": 1})
    assert not result.is_error
    data = result.structured_content
    assert data["level"] == "library"
    assert data["shelves"] == {
        "items": [{"id": 1, "name": "Ops", "url": f"{BASE_URL}/shelves/ops", "updated_at": "t1"}],
        "total": 3,
    }
    assert data["books"]["items"][0]["url"] == f"{BASE_URL}/books/guide"
    assert data["books"]["total"] == 1
    assert "offset=3" not in data["next"]  # 2 + 1 == 3 shelves: nothing more
    for request in fake_bookstack.requests:
        params = request.url.params
        assert params["sort"] == "name"
        assert params["count"] == "1"
        assert params["offset"] == "2"
        assert params["filter[name:like]"] == "%a\\%b%"


async def test_library_says_how_to_page_on(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/shelves", {"data": [], "total": 0})
    fake_bookstack.add(
        "GET", "/api/books", {"data": [{"id": 1, "name": "A", "slug": "a"}], "total": 5}
    )
    result = await _call(make_server, {"count": 1})
    assert "offset=1" in result.structured_content["next"]
    assert "filter[name:like]" not in fake_bookstack.requests[0].url.params


async def test_shelf_lists_its_books(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/shelves/4",
        {
            "id": 4,
            "name": "Ops",
            "slug": "ops",
            "books": [{"id": 7, "name": "Guide", "slug": "guide"}],
        },
    )
    data = (await _call(make_server, {"shelf_id": 4})).structured_content
    assert data["level"] == "shelf"
    assert data["url"] == f"{BASE_URL}/shelves/ops"
    assert data["books"]["items"] == [
        {"id": 7, "name": "Guide", "url": f"{BASE_URL}/books/guide", "updated_at": None}
    ]
    assert "book_id" in data["next"]


async def test_book_returns_tree_without_drafts(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/books/7",
        {
            "id": 7,
            "name": "Guide",
            "slug": "guide",
            "tags": [{"name": "team", "value": "ops"}, {"name": "flag", "value": None}],
            "contents": [
                {
                    "type": "chapter",
                    "id": 2,
                    "name": "Setup",
                    "slug": "setup",
                    "url": f"{BASE_URL}/books/guide/chapter/setup",
                    "pages": [
                        {"id": 10, "name": "Install", "draft": False, "url": "u10", "html": "x"},
                        {"id": 11, "name": "Draft", "draft": True, "url": "u11"},
                    ],
                },
                {"type": "page", "id": 12, "name": "FAQ", "draft": False, "url": "u12"},
                {"type": "page", "id": 13, "name": "Mine", "draft": True, "url": "u13"},
            ],
        },
    )
    data = (await _call(make_server, {"book_id": 7})).structured_content
    assert data["level"] == "book"
    assert data["url"] == f"{BASE_URL}/books/guide"
    assert data["tags"] == [{"name": "team", "value": "ops"}, {"name": "flag", "value": ""}]
    assert data["contents"] == [
        {
            "type": "chapter",
            "id": 2,
            "name": "Setup",
            "url": f"{BASE_URL}/books/guide/chapter/setup",
            "pages": [{"type": "page", "id": 10, "name": "Install", "url": "u10", "pages": None}],
        },
        {"type": "page", "id": 12, "name": "FAQ", "url": "u12", "pages": None},
    ]
    assert "chapter_id" in data["next"]


async def test_chapter_lists_pages_with_permanent_links(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/chapters/2",
        {
            "id": 2,
            "name": "Setup",
            "slug": "setup",
            "book_id": 7,
            "book_slug": "guide",
            "pages": [{"id": 10, "name": "Install", "slug": "install", "draft": False}],
        },
    )
    data = (await _call(make_server, {"chapter_id": 2})).structured_content
    assert data["level"] == "chapter"
    assert data["book_id"] == 7
    assert data["url"] == f"{BASE_URL}/books/guide/chapter/setup"
    assert data["pages"] == [
        {"id": 10, "name": "Install", "url": f"{BASE_URL}/link/10", "updated_at": None}
    ]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"shelf_id": 1, "book_id": 2}, "at most one id"),
        ({"book_id": 2, "name_contains": "x"}, "name_contains"),
    ],
)
async def test_invalid_combinations(
    make_server: MakeServer, fake_bookstack: FakeBookStack, args: dict[str, Any], message: str
) -> None:
    result = await _call(make_server, args)
    assert result.is_error
    assert message in result.content[0].text
    assert fake_bookstack.requests == []


async def test_count_is_bounded(make_server: MakeServer) -> None:
    result = await _call(make_server, {"count": 101})
    assert result.is_error


async def test_missing_book_is_not_found(make_server: MakeServer) -> None:
    result = await _call(make_server, {"book_id": 99})
    assert result.is_error
    assert "HTTP 404" in result.content[0].text
