"""The `search` tool through MCP: schema, request mapping, result shaping and errors."""

from __future__ import annotations

from typing import Any

from fastmcp import Client

from bookstack_mcp.tools.search import preview_text, search

from .conftest import FakeBookStack, MakeServer

PAGE_HIT: dict[str, Any] = {
    "id": 7,
    "name": "Release checklist",
    "slug": "release-checklist",
    "type": "page",
    "url": "https://docs.example.com/books/ops/page/release-checklist",
    "book": {"id": 2, "name": "Ops", "slug": "ops"},
    "chapter": {"id": 4, "name": "Releases", "slug": "releases"},
    "tags": [{"name": "status", "value": "draft", "order": 0}],
    "preview_html": {
        "name": "Release <strong>checklist</strong>",
        "content": "Run the <u>checklist</u> &amp; tag\n  the   release",
    },
}
BOOK_HIT: dict[str, Any] = {
    "id": 2,
    "name": "Ops",
    "type": "book",
    "url": "https://docs.example.com/books/ops",
    "tags": [],
    "preview_html": {"name": "Ops", "content": ""},
}


async def test_search_is_read_only_core(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[search], read_only=True)) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.name == "search"
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is True
    assert "{type:page|chapter}" in (listed.description or "")
    assert "deps" not in listed.input_schema["properties"]


async def test_unscoped_search(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    fake_bookstack.add("GET", "/api/search", {"data": [PAGE_HIT, BOOK_HIT], "total": 5})
    async with Client(make_server(tools=[search])) as mcp:
        result = await mcp.call_tool("search", {"query": "checklist", "count": 2})

    [request] = fake_bookstack.requests
    assert dict(request.url.params) == {"query": "checklist", "page": "1", "count": "2"}
    data = result.structured_content
    assert data is not None
    assert (data["total"], data["page"], data["count"], data["has_more"]) == (5, 1, 2, True)
    page, book = data["results"]
    assert page == {
        "type": "page",
        "id": 7,
        "name": "Release checklist",
        "url": PAGE_HIT["url"],
        "book": {"id": 2, "name": "Ops"},
        "chapter": {"id": 4, "name": "Releases"},
        "tags": [{"name": "status", "value": "draft"}],
        "preview": "Run the checklist & tag the release",
    }
    assert book["type"] == "book"
    assert book["book"] is None and book["chapter"] is None


async def test_last_page_has_no_more(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/search", {"data": [BOOK_HIT], "total": 3})
    async with Client(make_server(tools=[search])) as mcp:
        result = await mcp.call_tool("search", {"query": "ops", "page": 2, "count": 2})
    assert result.structured_content is not None
    assert result.structured_content["has_more"] is False


async def test_book_scope(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    fake_bookstack.add("GET", "/api/search/book/2", {"data": [PAGE_HIT], "total": 1})
    async with Client(make_server(tools=[search])) as mcp:
        result = await mcp.call_tool("search", {"query": "checklist", "book_id": 2})
    assert fake_bookstack.requests[0].url.path == "/api/search/book/2"
    assert result.structured_content is not None
    assert result.structured_content["total"] == 1


async def test_chapter_scope(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    fake_bookstack.add("GET", "/api/search/chapter/4", {"data": [], "total": 0})
    async with Client(make_server(tools=[search])) as mcp:
        result = await mcp.call_tool("search", {"query": "x", "chapter_id": 4})
    assert fake_bookstack.requests[0].url.path == "/api/search/chapter/4"
    assert result.structured_content is not None
    assert result.structured_content["results"] == []


async def test_scope_on_old_version(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    async with Client(make_server(tools=[search], version="v26.05.5")) as mcp:
        result = await mcp.call_tool("search", {"query": "x", "book_id": 2}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text
    assert "26.09" in text
    assert "without book_id" in text
    assert fake_bookstack.requests == []


async def test_unscoped_works_on_old_version(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/search", {"data": [], "total": 0})
    async with Client(make_server(tools=[search], version="v26.05.5")) as mcp:
        result = await mcp.call_tool("search", {"query": "x"})
    assert not result.is_error


async def test_both_scopes_rejected(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    async with Client(make_server(tools=[search])) as mcp:
        result = await mcp.call_tool(
            "search", {"query": "x", "book_id": 1, "chapter_id": 2}, raise_on_error=False
        )
    assert result.is_error
    assert "at most one" in result.content[0].text
    assert fake_bookstack.requests == []


async def test_input_limits(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    async with Client(make_server(tools=[search])) as mcp:
        for args in ({"query": ""}, {"query": "x" * 1001}, {"query": "x", "count": 101}):
            result = await mcp.call_tool("search", args, raise_on_error=False)
            assert result.is_error, args
    assert fake_bookstack.requests == []


async def test_bookstack_error_is_mapped(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/search/book/99",
        {"error": {"code": 404, "message": "Book not found"}},
        status=404,
    )
    async with Client(make_server(tools=[search])) as mcp:
        result = await mcp.call_tool("search", {"query": "x", "book_id": 99}, raise_on_error=False)
    assert result.is_error
    assert "Book not found" in result.content[0].text


def test_preview_keeps_highlighted_words_whole() -> None:
    content = "Call <strong>get</strong>_json() on https://x.test/<strong>api</strong>/pages"
    assert preview_text({"content": content}) == "Call get_json() on https://x.test/api/pages"


def test_preview_is_capped() -> None:
    text = preview_text({"content": "<p>" + "word " * 200 + "</p>"})
    assert len(text) <= 300
    assert text.endswith("…")
    assert preview_text(None) == ""
    assert preview_text({"content": None}) == ""
