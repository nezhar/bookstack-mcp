"""recent_changes through MCP: request shape, result mapping and errors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastmcp import Client

from bookstack_mcp.tools.recent_changes import recent_changes

from .conftest import BASE_URL, FakeBookStack, MakeServer

PAGE = {
    "id": 9,
    "name": "Runbook",
    "slug": "runbook",
    "book_id": 2,
    "chapter_id": 0,
    "draft": False,
    "updated_at": "2026-10-29T08:00:00.000000Z",
    "updated_by": 4,
}


async def test_filters_and_maps_pages(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/pages", {"data": [PAGE], "total": 3})
    async with Client(make_server(tools=[recent_changes])) as mcp:
        result = await mcp.call_tool(
            "recent_changes",
            {"since": "2026-10-28T10:30:00+02:00", "book_id": 2, "count": 1, "offset": 2},
        )

    params = dict(fake_bookstack.requests[0].url.params)
    assert params == {
        "count": "1",
        "offset": "2",
        "sort": "-updated_at",
        "filter[updated_at:gte]": "2026-10-28 08:30:00",
        "filter[draft]": "0",
        "filter[book_id]": "2",
    }
    content = result.structured_content
    assert content is not None
    assert content["since"] == "2026-10-28T08:30:00Z"
    assert content["total"] == 3
    assert content["pages"] == [
        {
            "page_id": 9,
            "name": "Runbook",
            "url": f"{BASE_URL}/link/9",
            "book_id": 2,
            "chapter_id": None,
            "updated_at": "2026-10-29T08:00:00.000000Z",
            "updated_by": 4,
        }
    ]


async def test_defaults_to_seven_days(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/pages", {"data": [], "total": 0})
    async with Client(make_server(tools=[recent_changes])) as mcp:
        await mcp.call_tool("recent_changes", {})

    params = dict(fake_bookstack.requests[0].url.params)
    assert "filter[book_id]" not in params
    assert params["count"] == "20"
    sent = datetime.strptime(params["filter[updated_at:gte]"], "%Y-%m-%d %H:%M:%S")
    expected = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)
    assert abs(sent - expected) < timedelta(minutes=1)


async def test_bad_since_is_a_tool_error(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    async with Client(make_server(tools=[recent_changes])) as mcp:
        result = await mcp.call_tool("recent_changes", {"since": "last week"}, raise_on_error=False)
    assert result.is_error
    assert "ISO" in result.content[0].text
    assert fake_bookstack.requests == []


async def test_read_only_and_core(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[recent_changes], read_only=True)) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.name == "recent_changes"
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is True
