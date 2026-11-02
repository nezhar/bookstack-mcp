"""create_page through the MCP protocol: arguments, request body, notes and error mapping."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Client

from bookstack_mcp.tools.create_page import create_page

from .conftest import BASE_URL, FakeBookStack, MakeServer


def _page(**overrides: Any) -> dict[str, Any]:
    page: dict[str, Any] = {
        "id": 7,
        "name": "Runbook",
        "book_id": 2,
        "chapter_id": 0,
        "editor": "markdown",
        "markdown": "# Hi",
        "raw_html": "<h1>Hi</h1>",
        "html": "<h1>Hi</h1>",
        "updated_at": "2026-10-30T10:00:00.000000Z",
    }
    page.update(overrides)
    return page


def _posted(fake: FakeBookStack) -> dict[str, Any]:
    [request] = [r for r in fake.requests if r.method == "POST"]
    body: dict[str, Any] = json.loads(request.content)
    return body


async def _call(server: Any, args: dict[str, Any]) -> Any:
    async with Client(server) as mcp:
        return await mcp.call_tool("create_page", args, raise_on_error=False)


async def test_annotations(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[create_page])) as mcp:
        [tool] = await mcp.list_tools()
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert "data:image/png;base64" in (tool.description or "")


async def test_hidden_when_read_only_or_core_disabled(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[create_page], read_only=True)) as mcp:
        assert await mcp.list_tools() == []
    async with Client(make_server(tools=[create_page], toolsets="structure")) as mcp:
        assert await mcp.list_tools() == []


async def test_markdown_in_book(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    fake_bookstack.add("POST", "/api/pages", _page())
    result = await _call(
        make_server(tools=[create_page]),
        {
            "name": "Runbook",
            "book_id": 2,
            "markdown": "# Hi",
            "tags": [{"name": "team", "value": "ops"}, {"name": "draft"}],
            "priority": 3,
        },
    )
    assert not result.is_error
    assert _posted(fake_bookstack) == {
        "name": "Runbook",
        "book_id": 2,
        "markdown": "# Hi",
        "tags": [{"name": "team", "value": "ops"}, {"name": "draft", "value": ""}],
        "priority": 3,
        "changelog": "Created via bookstack-mcp",
    }
    assert result.structured_content == {
        "page_id": 7,
        "name": "Runbook",
        "url": f"{BASE_URL}/link/7",
        "book_id": 2,
        "chapter_id": None,
        "editor": "markdown",
        "source_field": "markdown",
        "updated_at": "2026-10-30T10:00:00.000000Z",
        "notes": [],
    }


async def test_html_in_chapter_with_changelog(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("POST", "/api/pages", _page(chapter_id=5, editor="wysiwyg", markdown=""))
    result = await _call(
        make_server(tools=[create_page]),
        {"name": "Runbook", "chapter_id": 5, "html": "<p>x</p>", "changelog": "First"},
    )
    assert _posted(fake_bookstack) == {
        "name": "Runbook",
        "chapter_id": 5,
        "html": "<p>x</p>",
        "changelog": "First",
    }
    assert result.structured_content["chapter_id"] == 5
    assert result.structured_content["source_field"] == "html"
    assert result.structured_content["notes"] == []


async def test_template_copies_stored_source(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "GET",
        "/api/pages/9",
        _page(id=9, markdown="", raw_html="<p>{{a}}</p>", html="<p>expanded</p>"),
    )
    fake_bookstack.add("POST", "/api/pages", _page(markdown="", editor="wysiwyg"))
    result = await _call(
        make_server(tools=[create_page]), {"name": "Runbook", "book_id": 2, "template_page_id": 9}
    )
    assert not result.is_error
    body = _posted(fake_bookstack)
    assert body["html"] == "<p>{{a}}</p>"
    assert "markdown" not in body


async def test_content_wins_over_template(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("POST", "/api/pages", _page())
    result = await _call(
        make_server(tools=[create_page]),
        {"name": "Runbook", "book_id": 2, "markdown": "# Hi", "template_page_id": 9},
    )
    assert [r.method for r in fake_bookstack.requests] == ["POST"]
    assert result.structured_content["notes"] == [
        "Template page 9 was not used, because content was given."
    ]


async def test_markdown_note_when_editor_is_wysiwyg(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("POST", "/api/pages", _page(editor="wysiwyg"))
    result = await _call(
        make_server(tools=[create_page]), {"name": "Runbook", "book_id": 2, "markdown": "# Hi"}
    )
    [note] = result.structured_content["notes"]
    assert "editor-change" in note
    assert "WYSIWYG" in note


async def test_no_changelog_before_26_09(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("POST", "/api/pages", _page())
    server = make_server(tools=[create_page], version="v26.05.5")
    plain = await _call(server, {"name": "Runbook", "book_id": 2, "markdown": "# Hi"})
    assert "changelog" not in _posted(fake_bookstack)
    assert plain.structured_content["notes"] == []

    fake_bookstack.requests.clear()
    noted = await _call(
        server, {"name": "Runbook", "book_id": 2, "markdown": "# Hi", "changelog": "First"}
    )
    assert "changelog" not in _posted(fake_bookstack)
    [note] = noted.structured_content["notes"]
    assert "26.09" in note


async def test_argument_errors(make_server: MakeServer, fake_bookstack: FakeBookStack) -> None:
    server = make_server(tools=[create_page])
    cases = [
        ({"name": "x", "markdown": "a"}, "exactly one of book_id or chapter_id"),
        ({"name": "x", "book_id": 1, "chapter_id": 2, "markdown": "a"}, "exactly one"),
        ({"name": "x", "book_id": 1, "markdown": "a", "html": "b"}, "at most one"),
        ({"name": "x", "book_id": 1}, "No content given"),
    ]
    for args, message in cases:
        result = await _call(server, args)
        assert result.is_error
        assert message in result.content[0].text
    assert fake_bookstack.requests == []


async def test_empty_template_is_an_error(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/pages/9", _page(id=9, markdown="", raw_html="", editor=""))
    result = await _call(
        make_server(tools=[create_page]), {"name": "x", "book_id": 2, "template_page_id": 9}
    )
    assert result.is_error
    assert "has no content" in result.content[0].text


async def test_bookstack_errors_are_mapped(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "POST",
        "/api/pages",
        {
            "error": {
                "code": 422,
                "message": "The given data was invalid.",
                "validation": {"name": ["The name field is required."]},
            }
        },
        status=422,
    )
    result = await _call(
        make_server(tools=[create_page]), {"name": " ", "book_id": 2, "markdown": "a"}
    )
    assert result.is_error
    assert "name: The name field is required." in result.content[0].text

    fake_bookstack.add(
        "POST", "/api/pages", {"error": {"code": 403, "message": "No permission"}}, status=403
    )
    result = await _call(
        make_server(tools=[create_page]), {"name": "x", "book_id": 2, "markdown": "a"}
    )
    assert result.is_error
    assert "HTTP 403" in result.content[0].text
