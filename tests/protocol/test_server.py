"""The server's tool registration, visibility, version gating, errors and call logging.

The tools below are test doubles registered through the same `@tool` decorator and
`build_server` as the real ones; they are not shipped.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.config import Toolset
from bookstack_mcp.content.chunk import InvalidChunkRequest
from bookstack_mcp.editing.errors import SectionNotFound
from bookstack_mcp.server import INSTRUCTIONS, SERVER_NAME
from bookstack_mcp.tools import CurrentDeps, Deps, discover_tools, tool

from .conftest import FakeBookStack, MakeServer


class BookName(BaseModel):
    id: int
    name: str
    url: str


@tool(Toolset.CORE, idempotent=True)
async def read_book(
    book_id: Annotated[int, Field(description="The book's id.", ge=1)],
    deps: Deps = CurrentDeps(),
) -> BookName:
    """Read a book's name."""
    book = await deps.client.get_json(f"/books/{book_id}")
    return BookName(id=book["id"], name=book["name"], url=deps.links.book(book["slug"]))


@tool(Toolset.CORE, write=True)
async def rename_book(book_id: int, name: str, deps: Deps = CurrentDeps()) -> BookName:
    """Rename a book."""
    book = await deps.client.put_json(f"/books/{book_id}", {"name": name})
    return BookName(id=book["id"], name=book["name"], url=deps.links.book(book["slug"]))


@tool(Toolset.CORE, destructive=True)
async def delete_book(book_id: int, deps: Deps = CurrentDeps()) -> str:
    """Delete a book."""
    await deps.client.delete(f"/books/{book_id}")
    return "deleted"


@tool(Toolset.STRUCTURE)
async def list_shelves() -> list[str]:
    """List shelves."""
    return ["Handbooks"]


@tool(Toolset.CORE, needs={"scoped_search"})
async def search_in_book(book_id: int, query: str) -> list[str]:
    """Search one book."""
    return [query]


@tool(Toolset.CORE)
async def fails(kind: str) -> str:
    """Fail on purpose."""
    if kind == "section":
        raise SectionNotFound("Setup", ["Intro", "Usage"])
    if kind == "chunk":
        raise InvalidChunkRequest("offset 900 is past the end (500 characters)")
    if kind == "tool":
        raise ToolError("told you so")
    raise RuntimeError("secret internals")


ALL = [read_book, rename_book, delete_book, list_shelves, search_in_book, fails]


async def listed(server: Any) -> dict[str, Any]:
    async with Client(server) as mcp:
        return {t.name: t for t in await mcp.list_tools()}


# -- registration ----------------------------------------------------------------------


async def test_server_has_name_and_instructions(make_server: MakeServer) -> None:
    server = make_server(tools=[])
    assert server.name == SERVER_NAME
    assert server.instructions == INSTRUCTIONS
    assert "untrusted" in INSTRUCTIONS
    assert "edit_page" in INSTRUCTIONS
    assert "append_to_page" in INSTRUCTIONS


async def test_tags_and_annotations(make_server: MakeServer) -> None:
    tools = await listed(make_server(tools=ALL, allow_delete=True))

    read = tools["read_book"]
    assert read.meta["fastmcp"]["tags"] == ["core"]
    assert read.annotations.read_only_hint is True
    assert read.annotations.destructive_hint is False
    assert read.annotations.idempotent_hint is True
    assert read.annotations.open_world_hint is False
    assert read.description == "Read a book's name."
    assert "deps" not in read.input_schema["properties"]
    assert read.input_schema["properties"]["book_id"]["description"] == "The book's id."

    write = tools["rename_book"]
    assert sorted(write.meta["fastmcp"]["tags"]) == ["core", "write"]
    assert write.annotations.read_only_hint is False
    assert write.annotations.destructive_hint is False
    assert write.annotations.idempotent_hint is False

    delete = tools["delete_book"]
    assert sorted(delete.meta["fastmcp"]["tags"]) == ["core", "destructive", "write"]
    assert delete.annotations.destructive_hint is True


def test_tool_decorator_rejects_bad_declarations() -> None:
    with pytest.raises(ValueError, match="unknown features"):
        tool(Toolset.CORE, needs={"time_travel"})

    async def undocumented() -> None:
        pass

    with pytest.raises(ValueError, match="docstring"):
        tool(Toolset.CORE)(undocumented)


def test_discover_tools_finds_no_dummy_tools() -> None:
    names = {spec.name for spec in discover_tools()}
    assert not names & {spec.name for spec in ALL}


# -- visibility ------------------------------------------------------------------------


async def test_core_tool_is_listed_by_default(make_server: MakeServer) -> None:
    tools = await listed(make_server(tools=ALL))
    assert "read_book" in tools
    assert "list_shelves" not in tools


async def test_toolsets_hide_other_toolsets(make_server: MakeServer) -> None:
    tools = await listed(make_server(tools=ALL, toolsets="structure"))
    assert set(tools) == {"list_shelves"}


async def test_read_only_hides_write_tools(make_server: MakeServer) -> None:
    tools = await listed(make_server(tools=ALL, read_only=True, allow_delete=True))
    assert "read_book" in tools
    assert "rename_book" not in tools
    assert "delete_book" not in tools


async def test_destructive_tools_need_allow_delete(make_server: MakeServer) -> None:
    assert "delete_book" not in await listed(make_server(tools=ALL))
    assert "delete_book" in await listed(make_server(tools=ALL, allow_delete=True))


async def test_hidden_tool_cannot_be_called(make_server: MakeServer) -> None:
    async with Client(make_server(tools=ALL, read_only=True)) as mcp:
        with pytest.raises(ToolError, match="rename_book"):
            await mcp.call_tool("rename_book", {"book_id": 1, "name": "x"})


async def test_version_gating_hides_tools_and_logs_them(
    make_server: MakeServer, caplog: pytest.LogCaptureFixture
) -> None:
    assert "search_in_book" in await listed(make_server(tools=ALL, version="v26.09.2"))

    caplog.set_level("INFO", logger="bookstack_mcp")
    tools = await listed(make_server(tools=ALL, version="v26.05.5"))
    assert "search_in_book" not in tools
    assert "read_book" in tools
    [record] = [r for r in caplog.records if r.getMessage().startswith("tools disabled")]
    assert record.__dict__["tools"] == ["search_in_book"]
    assert record.__dict__["version"] == "v26.05.5"


# -- calls -----------------------------------------------------------------------------


async def test_tool_reaches_the_client_through_deps(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add("GET", "/api/books/7", {"id": 7, "name": "Guide", "slug": "guide"})
    async with Client(make_server(tools=ALL)) as mcp:
        result = await mcp.call_tool("read_book", {"book_id": 7})
    assert result.structured_content == {
        "id": 7,
        "name": "Guide",
        "url": "https://docs.example.com/books/guide",
    }


async def test_unknown_arguments_are_ignored_and_logged(
    make_server: MakeServer, fake_bookstack: FakeBookStack, json_logs: Any
) -> None:
    fake_bookstack.add("GET", "/api/books/7", {"id": 7, "name": "Guide", "slug": "guide"})
    server = make_server(tools=ALL)
    tools = await listed(server)
    assert tools["read_book"].input_schema.get("additionalProperties") is not False

    async with Client(server) as mcp:
        result = await mcp.call_tool(
            "read_book", {"book_id": 7, "verbose": "do-not-log-me", "trace": 1}
        )
    assert result.is_error is False
    assert result.structured_content["name"] == "Guide"

    records = json_logs()
    [ignored] = [r for r in records if r["msg"] == "ignored unknown tool arguments"]
    assert ignored["level"] == "DEBUG"
    assert ignored["tool"] == "read_book"
    assert ignored["ignored_arguments"] == ["trace", "verbose"]
    assert not any("do-not-log-me" in str(r) for r in records)


async def test_invalid_arguments_are_still_rejected(make_server: MakeServer) -> None:
    async with Client(make_server(tools=ALL)) as mcp:
        with pytest.raises(ToolError):
            await mcp.call_tool("read_book", {"book_id": 0})


# -- errors ----------------------------------------------------------------------------


async def test_bookstack_validation_error_becomes_tool_error(
    make_server: MakeServer, fake_bookstack: FakeBookStack
) -> None:
    fake_bookstack.add(
        "PUT",
        "/api/books/7",
        {
            "error": {
                "code": 422,
                "message": "The given data was invalid.",
                "validation": {"name": ["The name may not be greater than 255 characters."]},
            }
        },
        status=422,
    )
    async with Client(make_server(tools=ALL)) as mcp:
        result = await mcp.call_tool(
            "rename_book", {"book_id": 7, "name": "x"}, raise_on_error=False
        )
    assert result.is_error
    text = result.content[0].text
    assert "HTTP 422" in text
    assert "The given data was invalid." in text
    assert "name: The name may not be greater than 255 characters." in text
    assert "Hint: Fix the fields listed" in text


async def test_bookstack_not_found_becomes_tool_error(make_server: MakeServer) -> None:
    async with Client(make_server(tools=ALL)) as mcp:
        result = await mcp.call_tool("read_book", {"book_id": 99}, raise_on_error=False)
    assert result.is_error
    text = result.content[0].text
    assert "HTTP 404" in text
    assert "Not found" in text
    assert "Hint: Check the id" in text


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("section", "No section matches 'Setup'. Available headings: 'Intro', 'Usage'"),
        ("chunk", "offset 900 is past the end"),
        ("tool", "told you so"),
    ],
)
async def test_expected_errors_reach_the_model(
    make_server: MakeServer, kind: str, expected: str
) -> None:
    async with Client(make_server(tools=ALL)) as mcp:
        result = await mcp.call_tool("fails", {"kind": kind}, raise_on_error=False)
    assert result.is_error
    assert expected in result.content[0].text


async def test_unexpected_errors_are_masked(make_server: MakeServer) -> None:
    async with Client(make_server(tools=ALL)) as mcp:
        result = await mcp.call_tool("fails", {"kind": "bug"}, raise_on_error=False)
    assert result.is_error
    assert "secret internals" not in result.content[0].text


# -- call logging ----------------------------------------------------------------------


async def test_one_log_line_per_call_with_request_id(
    make_server: MakeServer, fake_bookstack: FakeBookStack, json_logs: Any
) -> None:
    fake_bookstack.add("GET", "/api/books/7", {"id": 7, "name": "Guide", "slug": "guide"})
    async with Client(make_server(tools=ALL)) as mcp:
        await mcp.call_tool("read_book", {"book_id": 7})
        await mcp.call_tool("fails", {"kind": "section"}, raise_on_error=False)

    records = json_logs()
    calls = [r for r in records if r["msg"] == "tool call"]
    assert [(c["tool"], c["outcome"]) for c in calls] == [
        ("read_book", "ok"),
        ("fails", "SectionNotFound"),
    ]
    assert all(isinstance(c["duration_ms"], int) for c in calls)
    assert calls[0]["request_id"] != calls[1]["request_id"]
    assert set(calls[0]) == {
        "ts",
        "level",
        "logger",
        "msg",
        "tool",
        "request_id",
        "duration_ms",
        "outcome",
    }

    [request] = [r for r in records if r["msg"] == "bookstack request"]
    assert request["path"] == "/api/books/7"
    assert request["request_id"] == calls[0]["request_id"]
