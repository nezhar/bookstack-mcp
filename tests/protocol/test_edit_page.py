"""edit_page through MCP: request shape, guards, error details and the result fields.

The fake below only stores what was PUT so the re-read sees it; what BookStack really
stores is proven by tests/integration/test_edit_page.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.tools.edit_page import edit_page

from .conftest import FakeBookStack, MakeServer

UPDATED = "2026-10-01T10:00:00.000000Z"
LATER = "2026-10-02T10:00:00.000000Z"


class PageStore(FakeBookStack):
    """A fake that keeps pages: GET returns the current page, PUT updates it."""

    def __init__(self) -> None:
        super().__init__()
        self.pages: dict[int, dict[str, Any]] = {}
        self.puts: list[dict[str, Any]] = []
        self.ignore_puts = False
        self.markdown_as_html = False

    def page(self, page_id: int, **fields: Any) -> dict[str, Any]:
        page = {
            "id": page_id,
            "name": f"Page {page_id}",
            "markdown": "",
            "raw_html": "",
            "html": "",
            "editor": "markdown",
            "updated_at": UPDATED,
            "revision_count": 3,
        }
        page.update(fields)
        self.pages[page_id] = page
        return page

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        match = re.fullmatch(r"/api/pages/(\d+)", request.url.path)
        if match is None or int(match.group(1)) not in self.pages:
            return super().handler(request)
        self.requests.append(request)
        page = self.pages[int(match.group(1))]
        if request.method == "PUT":
            body = json.loads(request.content)
            self.puts.append(body)
            if not self.ignore_puts:
                if "markdown" in body and self.markdown_as_html:
                    page["markdown"] = ""
                    page["raw_html"] = f"<p>{body['markdown']}</p>"
                elif "markdown" in body:
                    page["markdown"] = body["markdown"]
                if "html" in body:
                    page["markdown"] = ""
                    page["raw_html"] = re.sub(
                        r"<p>", lambda _: f'<p id="bkmrk-{len(self.puts)}">', body["html"]
                    )
                page["updated_at"] = LATER
                page["revision_count"] += 1
        return httpx2.Response(200, json=page)


@pytest.fixture
def fake_bookstack() -> PageStore:
    return PageStore()


async def call(server: Any, **args: Any) -> Any:
    async with Client(server) as mcp:
        return await mcp.call_tool("edit_page", args, raise_on_error=False)


async def test_markdown_edit_writes_only_the_source_field(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="# Title\n\nHello world.\n")
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[{"old_string": "Hello world.", "new_string": "Hello there."}],
        expected_updated_at=UPDATED,
    )
    assert not result.is_error
    assert fake_bookstack.puts == [
        {
            "markdown": "# Title\n\nHello there.\n",
            "changelog": "Edited via bookstack-mcp: 1 change(s)",
        }
    ]
    data = result.structured_content
    assert data["verified"] is True
    assert data["written"] is True
    assert data["dry_run"] is False
    assert data["unverified_count"] == 0
    assert data["source_field"] == "markdown"
    assert (data["chars_before"], data["chars_after"], data["delta"]) == (22, 22, 0)
    assert data["updated_at"] == LATER
    assert data["revision_count"] == 4
    assert data["url"] == "https://docs.example.com/link/7"
    assert data["name"] == "Page 7"
    assert data["contexts"] == [
        {"index": 0, "occurrences_replaced": 1, "context": "# Title\n\nHello there.\n"}
    ]
    assert data["diff"] == "@@ -3 +3 @@\n-Hello world.\n+Hello there."
    assert not {"markdown", "html", "raw_html"} & data.keys()


async def test_html_page_is_written_as_html_and_verifies_despite_ids(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(8, raw_html="<p>One</p>\n<p>Two</p>", html="rendered", editor="wysiwyg")
    result = await call(
        make_server(tools=[edit_page]),
        page_id=8,
        edits=[{"old_string": "<p>Two</p>", "new_string": "<p>Deux</p>"}],
        changelog="Translate",
    )
    assert fake_bookstack.puts == [{"html": "<p>One</p>\n<p>Deux</p>", "changelog": "Translate"}]
    assert 'id="bkmrk-1"' in fake_bookstack.pages[8]["raw_html"]
    assert result.structured_content["source_field"] == "html"
    assert result.structured_content["verified"] is True


async def test_markdown_wins_over_a_wysiwyg_editor_field(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(9, markdown="a b c", raw_html="<p>a b c</p>", editor="wysiwyg")
    result = await call(
        make_server(tools=[edit_page]),
        page_id=9,
        edits=[{"old_string": "b", "new_string": "B"}],
    )
    assert list(fake_bookstack.puts[0]) == ["markdown", "changelog"]
    assert result.structured_content["source_field"] == "markdown"


async def test_several_edits_and_replace_all(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="x y x w x")
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[
            {"old_string": "x", "new_string": "z", "replace_all": True},
            {"old_string": "z y z", "new_string": "z-y-z"},
        ],
    )
    assert fake_bookstack.puts[0]["markdown"] == "z-y-z w z"
    assert fake_bookstack.puts[0]["changelog"] == "Edited via bookstack-mcp: 2 change(s)"
    contexts = result.structured_content["contexts"]
    assert [(c["index"], c["occurrences_replaced"]) for c in contexts] == [(0, 3), (1, 1)]


async def test_changelog_is_not_sent_before_26_09(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="old text here")
    result = await call(
        make_server(tools=[edit_page], version="v26.05.5"),
        page_id=7,
        edits=[{"old_string": "old", "new_string": "new"}],
    )
    assert fake_bookstack.puts == [{"markdown": "new text here"}]
    assert any("changelog not sent" in n for n in result.structured_content["notes"])


async def test_dry_run_writes_nothing(make_server: MakeServer, fake_bookstack: PageStore) -> None:
    fake_bookstack.page(7, markdown="old text here")
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[{"old_string": "old", "new_string": "new"}],
        dry_run=True,
    )
    assert fake_bookstack.puts == []
    assert [r.method for r in fake_bookstack.requests] == ["GET"]
    data = result.structured_content
    assert (data["dry_run"], data["written"], data["verified"]) == (True, False, None)
    assert data["updated_at"] == UPDATED
    assert data["revision_count"] == 3
    assert data["diff"] == "@@ -1 +1 @@\n-old text here\n+new text here"
    assert data["notes"] == ["Dry run: nothing was written."]


async def test_stale_page_is_refused_with_both_timestamps(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="old text", updated_at=LATER)
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[{"old_string": "old", "new_string": "new"}],
        expected_updated_at=UPDATED,
    )
    assert result.is_error
    message = result.content[0].text
    assert LATER in message
    assert UPDATED in message
    assert "Read the page again and rebuild the anchors." in message
    assert fake_bookstack.puts == []


async def test_ambiguous_anchor_lists_the_first_three_matches(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="\n".join(f"item {n}: TODO" for n in range(5)))
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[{"old_string": "TODO", "new_string": "done"}],
    )
    assert result.is_error
    message = result.content[0].text
    assert "occurs 5 times" in message
    assert "1. " in message and "2. " in message and "3. " in message
    assert "4. " not in message
    assert "replace_all" in message
    assert fake_bookstack.puts == []


async def test_missing_anchor_shows_the_near_miss(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(8, raw_html="<p>Hello\n   world</p>")
    result = await call(
        make_server(tools=[edit_page]),
        page_id=8,
        edits=[{"old_string": "Hello world", "new_string": "Hi"}],
    )
    assert result.is_error
    assert "'Hello\\n   world'" in result.content[0].text


async def test_shrink_is_refused_with_both_sizes(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="keep " + "x" * 95)
    args: dict[str, Any] = {
        "page_id": 7,
        "edits": [{"old_string": "x" * 95, "new_string": ""}],
    }
    result = await call(make_server(tools=[edit_page]), **args)
    assert result.is_error
    assert "from 100 to 5 characters" in result.content[0].text
    assert fake_bookstack.puts == []

    result = await call(make_server(tools=[edit_page]), **args, allow_shrink=True)
    assert not result.is_error
    assert fake_bookstack.puts[0]["markdown"] == "keep "


async def test_unstored_change_is_reported_unverified(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="old text here")
    fake_bookstack.ignore_puts = True
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[{"old_string": "old", "new_string": "new"}],
    )
    data = result.structured_content
    assert (data["written"], data["verified"], data["unverified_count"]) == (True, False, 1)
    assert any("verification check(s) failed" in n for n in data["notes"])


async def test_stored_in_another_format_is_unverified(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="old text here")
    fake_bookstack.markdown_as_html = True
    result = await call(
        make_server(tools=[edit_page]),
        page_id=7,
        edits=[{"old_string": "old", "new_string": "new"}],
    )
    data = result.structured_content
    assert (data["verified"], data["unverified_count"]) == (False, 1)
    assert any("stored it as html" in n for n in data["notes"])


@pytest.mark.parametrize("count", [0, 21])
async def test_edit_count_is_limited(
    make_server: MakeServer, fake_bookstack: PageStore, count: int
) -> None:
    fake_bookstack.page(7, markdown="text")
    edits = [{"old_string": f"a{n}", "new_string": "b"} for n in range(count)]
    result = await call(make_server(tools=[edit_page]), page_id=7, edits=edits)
    assert result.is_error
    assert fake_bookstack.requests == []


async def test_annotations_and_read_only_mode(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[edit_page])) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is False
    assert listed.annotations.destructive_hint is False
    assert "deps" not in listed.input_schema["properties"]
    async with Client(make_server(tools=[edit_page], read_only=True)) as mcp:
        assert await mcp.list_tools() == []
