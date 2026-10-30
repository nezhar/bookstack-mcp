"""list_tags through MCP: request shape, result mapping, gating and errors."""

from __future__ import annotations

from fastmcp import Client

from bookstack_mcp.tools.list_tags import list_tags

from .conftest import FakeBookStack, MakeServer

COUNTS = {"usages": 5, "page_count": 3, "chapter_count": 1, "book_count": 1, "shelf_count": 0}


async def test_lists_names_by_usage(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    fake_bookstack.add(
        "GET",
        "/api/tags/names",
        {"data": [{"name": "Status", "values": 2, **COUNTS}], "total": 7},
    )
    async with Client(make_server(tools=[list_tags])) as mcp:
        result = await mcp.call_tool("list_tags", {"name_contains": "Sta", "count": 10})

    params = dict(fake_bookstack.requests[0].url.params)
    assert params == {
        "count": "10",
        "offset": "0",
        "sort": "-usages",
        "filter[name:like]": "%Sta%",
    }
    content = result.structured_content
    assert content is not None
    assert content["names"] == [{"name": "Status", "values": 2, **COUNTS}]
    assert content["values"] == []
    assert content["total"] == 7


async def test_lists_values_for_a_name(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/tags/values-for-name",
        {"data": [{"name": "Status", "value": "Draft", **COUNTS}], "total": 1},
    )
    async with Client(make_server(tools=[list_tags])) as mcp:
        result = await mcp.call_tool("list_tags", {"name": "Status", "offset": 50})

    params = dict(fake_bookstack.requests[0].url.params)
    assert params == {"name": "Status", "count": "50", "offset": "50", "sort": "-usages"}
    content = result.structured_content
    assert content is not None
    assert content["name"] == "Status"
    assert content["values"] == [{"value": "Draft", **COUNTS}]
    assert content["names"] == []


async def test_name_and_name_contains_conflict(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    async with Client(make_server(tools=[list_tags])) as mcp:
        result = await mcp.call_tool(
            "list_tags", {"name": "Status", "name_contains": "x"}, raise_on_error=False
        )
    assert result.is_error
    assert fake_bookstack.requests == []


async def test_count_is_bounded(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[list_tags])) as mcp:
        result = await mcp.call_tool("list_tags", {"count": 501}, raise_on_error=False)
    assert result.is_error


async def test_hidden_before_26_05(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[list_tags], version="v25.12.1")) as mcp:
        assert "list_tags" not in {t.name for t in await mcp.list_tools()}
    async with Client(make_server(tools=[list_tags], version="v26.05.5")) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is True


async def test_forbidden_maps_to_tool_error(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET", "/api/tags/names", {"error": {"code": 403, "message": "No permission"}}, 403
    )
    async with Client(make_server(tools=[list_tags])) as mcp:
        result = await mcp.call_tool("list_tags", {}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text
    assert "403" in text
    assert "No permission" in text
