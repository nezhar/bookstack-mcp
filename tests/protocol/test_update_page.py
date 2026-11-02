"""update_page through MCP: request shape, input rules, guards and the result fields.

The fake stores what was PUT so the re-read sees it; what BookStack really stores is
proven by tests/integration/test_update_page.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.tools.update_page import update_page

from .conftest import MakeServer
from .test_edit_page import LATER, UPDATED, PageStore


class MetaPageStore(PageStore):
    """PageStore that also applies name, tags, moves and priority from a PUT."""

    def __init__(self) -> None:
        super().__init__()
        self.ignore_meta = False

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        match = re.fullmatch(r"/api/pages/(\d+)", request.url.path)
        if (
            request.method == "PUT"
            and match is not None
            and int(match.group(1)) in self.pages
            and not self.ignore_meta
        ):
            page = self.pages[int(match.group(1))]
            body = json.loads(request.content)
            for key in ("name", "tags", "priority"):
                if key in body:
                    page[key] = body[key]
            if "book_id" in body:
                page["book_id"], page["chapter_id"] = body["book_id"], 0
            if "chapter_id" in body:
                page["chapter_id"] = body["chapter_id"]
        return super().handler(request)


@pytest.fixture
def fake_bookstack() -> MetaPageStore:
    return MetaPageStore()


def add_page(store: MetaPageStore, page_id: int, **fields: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {"book_id": 1, "chapter_id": 0, "priority": 2, "tags": []}
    return store.page(page_id, **(defaults | fields))


async def call(server: Any, **args: Any) -> Any:
    async with Client(server) as mcp:
        return await mcp.call_tool("update_page", args, raise_on_error=False)


async def test_rename_only_sends_name_and_changelog(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="text")
    result = await call(make_server(tools=[update_page]), page_id=7, name="New name")
    assert not result.is_error, result.content
    assert fake_bookstack.puts == [{"name": "New name", "changelog": "Updated via bookstack-mcp"}]
    assert [r.method for r in fake_bookstack.requests] == ["PUT", "GET"]
    data = result.structured_content
    assert data["changed"] == ["name"]
    assert data["name"] == "New name"
    assert (data["written"], data["verified"], data["dry_run"]) == (True, True, False)
    assert data["content"] is None
    assert data["location"] is None
    assert data["tags"] is None
    assert data["updated_at"] == LATER
    assert data["revision_count"] == 4
    assert data["url"] == "https://docs.example.com/link/7"


async def test_markdown_replacement_with_metadata_in_one_put(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="# Old\n\nOld body.")
    result = await call(
        make_server(tools=[update_page]),
        page_id=7,
        name="Renamed",
        markdown="# New\n\nNew body.",
        tags=[{"name": "status", "value": "draft"}],
        expected_updated_at=UPDATED,
        changelog="Rewrite",
    )
    assert not result.is_error, result.content
    assert fake_bookstack.puts == [
        {
            "name": "Renamed",
            "tags": [{"name": "status", "value": "draft"}],
            "markdown": "# New\n\nNew body.",
            "changelog": "Rewrite",
        }
    ]
    data = result.structured_content
    assert data["changed"] == ["name", "markdown", "tags"]
    assert data["verified"] is True
    assert data["name"] == "Renamed"
    assert data["tags"] == [{"name": "status", "value": "draft"}]
    assert data["content"]["source_field"] == "markdown"
    assert data["content"]["verified"] is True
    assert "@@" in data["content"]["diff"]
    assert not {"markdown", "html", "raw_html"} & data.keys()


async def test_html_page_takes_html(make_server: MakeServer, fake_bookstack: MetaPageStore) -> None:
    add_page(fake_bookstack, 8, raw_html="<p>Old</p>", editor="wysiwyg")
    result = await call(make_server(tools=[update_page]), page_id=8, html="<p>New text</p>")
    assert not result.is_error, result.content
    assert fake_bookstack.puts[0]["html"] == "<p>New text</p>"
    assert result.structured_content["verified"] is True


@pytest.mark.parametrize(
    ("stored", "args", "sent"),
    [
        ({"markdown": "# Text"}, {"html": "<p>Text</p>"}, "html"),
        ({"raw_html": "<p>Text</p>", "editor": "wysiwyg"}, {"markdown": "Text here"}, "markdown"),
    ],
)
async def test_format_change_needs_the_flag(
    make_server: MakeServer,
    fake_bookstack: MetaPageStore,
    stored: dict[str, str],
    args: dict[str, str],
    sent: str,
) -> None:
    add_page(fake_bookstack, 7, **stored)
    result = await call(make_server(tools=[update_page]), page_id=7, **args)
    assert result.is_error
    message = result.content[0].text
    assert "allow_format_change" in message
    assert f"sending {sent}" in message
    assert fake_bookstack.puts == []

    result = await call(
        make_server(tools=[update_page]), page_id=7, allow_format_change=True, **args
    )
    assert not result.is_error, result.content
    assert list(fake_bookstack.puts[0]) == [sent, "changelog"]
    data = result.structured_content
    assert data["content"]["source_field"] == sent
    assert data["verified"] is True
    assert any("converted" in n for n in data["notes"])


async def test_shrink_is_refused_without_allow_shrink(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="x" * 100)
    result = await call(make_server(tools=[update_page]), page_id=7, markdown="short")
    assert result.is_error
    assert "from 100 to 5 characters" in result.content[0].text
    assert fake_bookstack.puts == []

    result = await call(
        make_server(tools=[update_page]), page_id=7, markdown="short", allow_shrink=True
    )
    assert not result.is_error
    assert fake_bookstack.puts[0]["markdown"] == "short"


@pytest.mark.parametrize("markdown", [None, "new text"])
async def test_stale_page_is_refused(
    make_server: MakeServer, fake_bookstack: MetaPageStore, markdown: str | None
) -> None:
    add_page(fake_bookstack, 7, markdown="old text", updated_at=LATER)
    args: dict[str, Any] = {"name": "X"} if markdown is None else {"markdown": markdown}
    result = await call(
        make_server(tools=[update_page]), page_id=7, expected_updated_at=UPDATED, **args
    )
    assert result.is_error
    assert LATER in result.content[0].text and UPDATED in result.content[0].text
    assert fake_bookstack.puts == []


@pytest.mark.parametrize(
    ("move", "location"),
    [
        ({"book_id": 5}, {"book_id": 5, "chapter_id": None}),
        ({"chapter_id": 9}, {"book_id": 1, "chapter_id": 9}),
    ],
)
async def test_move_reports_the_new_location(
    make_server: MakeServer,
    fake_bookstack: MetaPageStore,
    move: dict[str, int],
    location: dict[str, Any],
) -> None:
    add_page(fake_bookstack, 7, markdown="text", chapter_id=3)
    result = await call(make_server(tools=[update_page]), page_id=7, priority=4, **move)
    assert not result.is_error, result.content
    assert fake_bookstack.puts == [
        {**move, "priority": 4, "changelog": "Updated via bookstack-mcp"}
    ]
    data = result.structured_content
    assert data["location"] == location
    assert data["changed"] == [*move, "priority"]
    assert data["verified"] is True


async def test_empty_tag_list_clears_tags(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="text", tags=[{"name": "a", "value": "1"}])
    result = await call(make_server(tools=[update_page]), page_id=7, tags=[])
    assert fake_bookstack.puts[0]["tags"] == []
    assert result.structured_content["tags"] == []


async def test_unstored_metadata_is_unverified(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="text")
    fake_bookstack.ignore_meta = True
    result = await call(
        make_server(tools=[update_page]),
        page_id=7,
        name="New",
        tags=[{"name": "t"}],
        chapter_id=9,
    )
    data = result.structured_content
    assert (data["written"], data["verified"]) == (True, False)
    notes = " ".join(data["notes"])
    assert "name:" in notes and "tags:" in notes and "chapter_id:" in notes


async def test_dry_run_writes_nothing(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="old text here")
    for args in ({"name": "New"}, {"markdown": "new text here", "tags": []}):
        result = await call(make_server(tools=[update_page]), page_id=7, dry_run=True, **args)
        assert not result.is_error, result.content
        data = result.structured_content
        assert (data["dry_run"], data["written"], data["verified"]) == (True, False, None)
        assert data["updated_at"] == UPDATED
        assert "Dry run: nothing was written." in data["notes"]
    assert fake_bookstack.puts == []
    assert {r.method for r in fake_bookstack.requests} == {"GET"}


async def test_changelog_is_not_sent_before_26_09(
    make_server: MakeServer, fake_bookstack: MetaPageStore
) -> None:
    add_page(fake_bookstack, 7, markdown="text")
    result = await call(make_server(tools=[update_page], version="v26.05.5"), page_id=7, name="N")
    assert fake_bookstack.puts == [{"name": "N"}]
    assert any("changelog not sent" in n for n in result.structured_content["notes"])


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"expected_updated_at": UPDATED, "dry_run": True},
        {"markdown": "a", "html": "<p>a</p>"},
        {"book_id": 1, "chapter_id": 2},
        {"name": ""},
        {"name": "x" * 256},
    ],
)
async def test_invalid_input_is_refused_before_any_request(
    make_server: MakeServer, fake_bookstack: MetaPageStore, args: dict[str, Any]
) -> None:
    add_page(fake_bookstack, 7, markdown="text")
    result = await call(make_server(tools=[update_page]), page_id=7, **args)
    assert result.is_error
    assert fake_bookstack.requests == []


async def test_annotations_and_read_only_mode(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[update_page])) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is False
    assert listed.annotations.destructive_hint is False
    assert "deps" not in listed.input_schema["properties"]
    assert "replaces ALL" in listed.input_schema["properties"]["tags"]["description"]
    assert "REPLACES THE WHOLE TAG SET" in " ".join((listed.description or "").split())
    async with Client(make_server(tools=[update_page], read_only=True)) as mcp:
        assert await mcp.list_tools() == []
