"""append_to_page through MCP: request shape, positions, error details and the result fields.

The fake below only stores what was PUT so the re-read sees it; what BookStack really
stores is proven by tests/integration/test_append_to_page.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.tools.append_to_page import append_to_page

from .conftest import FakeBookStack, MakeServer

UPDATED = "2026-10-01T10:00:00.000000Z"
LATER = "2026-10-02T10:00:00.000000Z"
OUTLINE_PAGE = (
    "# Guide\n\nIntro.\n\n## Setup\n\nInstall.\n\n### Linux\n\napt.\n\n## Usage\n\nRun.\n"
)


class PageStore(FakeBookStack):
    """A fake that keeps pages: GET returns the current page, PUT updates it."""

    def __init__(self) -> None:
        super().__init__()
        self.pages: dict[int, dict[str, Any]] = {}
        self.puts: list[dict[str, Any]] = []
        self.ignore_puts = False

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
                if "markdown" in body:
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
        return await mcp.call_tool("append_to_page", args, raise_on_error=False)


async def test_end_append_writes_only_the_source_field(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="# Title\n\nHello.\n")
    result = await call(
        make_server(tools=[append_to_page]),
        page_id=7,
        content="New line.",
        expected_updated_at=UPDATED,
    )
    assert not result.is_error, result.content
    assert fake_bookstack.puts == [
        {"markdown": "# Title\n\nHello.\n\nNew line.\n", "changelog": "Appended via bookstack-mcp"}
    ]
    data = result.structured_content
    assert (data["written"], data["verified"], data["unverified_count"]) == (True, True, 0)
    assert (data["position"], data["section"]) == ("end", None)
    assert (data["chars_before"], data["chars_after"], data["delta"]) == (16, 27, 11)
    assert (data["updated_at"], data["revision_count"]) == (LATER, 4)
    assert data["source_field"] == "markdown"
    assert data["url"] == "https://docs.example.com/link/7"
    assert data["contexts"] == [
        {"index": 0, "occurrences_replaced": 1, "context": "# Title\n\nHello.\n\nNew line.\n"}
    ]
    assert "+New line." in data["diff"]
    assert not {"markdown", "html", "raw_html"} & data.keys()


async def test_section_end_lands_after_the_subsection(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown=OUTLINE_PAGE)
    result = await call(
        make_server(tools=[append_to_page]),
        page_id=7,
        content="Also Windows.",
        position="section_end",
        section="Setup",
        changelog="Add Windows",
    )
    assert not result.is_error, result.content
    assert fake_bookstack.puts[0] == {
        "markdown": OUTLINE_PAGE.replace("apt.\n", "apt.\n\nAlso Windows.\n"),
        "changelog": "Add Windows",
    }
    data = result.structured_content
    assert (data["position"], data["section"]) == ("section_end", "Setup")
    assert "apt.\n\nAlso Windows.\n\n## Usage" in data["contexts"][0]["context"]


async def test_after_heading_lands_below_the_heading(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown=OUTLINE_PAGE)
    result = await call(
        make_server(tools=[append_to_page]),
        page_id=7,
        content="Read this first.",
        position="after_heading",
        section="Setup",
        separator="\n\n",
    )
    assert not result.is_error, result.content
    assert fake_bookstack.puts[0]["markdown"] == OUTLINE_PAGE.replace(
        "## Setup\n", "## Setup\n\nRead this first.\n"
    )


async def test_plain_text_on_html_page_becomes_paragraphs(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(8, raw_html="<p>One</p>", html="rendered", editor="wysiwyg")
    result = await call(
        make_server(tools=[append_to_page]), page_id=8, content="Two & three\n\nFour"
    )
    assert not result.is_error, result.content
    assert fake_bookstack.puts[0]["html"] == "<p>One</p>\n<p>Two &amp; three</p>\n<p>Four</p>"
    assert result.structured_content["source_field"] == "html"
    assert result.structured_content["verified"] is True


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ("Missing", "No section matches 'Missing'. Available headings:"),
        ("U", "matches several headings"),
    ],
)
async def test_bad_section_lists_headings_and_points_to_the_outline(
    make_server: MakeServer, fake_bookstack: PageStore, section: str, expected: str
) -> None:
    fake_bookstack.page(7, markdown="# Guide\n\n## Usage\n\n## Upgrade\n")
    result = await call(
        make_server(tools=[append_to_page]),
        page_id=7,
        content="x",
        position="section_end",
        section=section,
    )
    assert result.is_error
    message = result.content[0].text
    assert expected in message
    assert "'Usage'" in message and "'Upgrade'" in message
    assert 'read_page with format="outline"' in message
    assert fake_bookstack.puts == []


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"position": "section_end"}, "needs a section name"),
        ({"position": "end", "section": "Usage"}, "section is only used"),
        ({"content": "  \n "}, "content must not be empty"),
    ],
)
async def test_invalid_arguments_are_refused(
    make_server: MakeServer, fake_bookstack: PageStore, args: dict[str, Any], expected: str
) -> None:
    fake_bookstack.page(7, markdown="# Guide\n\n## Usage\n")
    result = await call(make_server(tools=[append_to_page]), page_id=7, **{"content": "x", **args})
    assert result.is_error
    assert expected in result.content[0].text
    assert fake_bookstack.puts == []


@pytest.mark.parametrize("args", [{"content": ""}, {"position": "start"}, {"changelog": "x" * 181}])
async def test_schema_rejects_bad_input_before_any_request(
    make_server: MakeServer, fake_bookstack: PageStore, args: dict[str, Any]
) -> None:
    fake_bookstack.page(7, markdown="text")
    result = await call(make_server(tools=[append_to_page]), page_id=7, **{"content": "x", **args})
    assert result.is_error
    assert fake_bookstack.requests == []


async def test_dry_run_writes_nothing(make_server: MakeServer, fake_bookstack: PageStore) -> None:
    fake_bookstack.page(7, markdown="text")
    result = await call(
        make_server(tools=[append_to_page]), page_id=7, content="more", dry_run=True
    )
    assert fake_bookstack.puts == []
    assert [r.method for r in fake_bookstack.requests] == ["GET"]
    data = result.structured_content
    assert (data["dry_run"], data["written"], data["verified"]) == (True, False, None)
    assert "+more" in data["diff"]
    assert data["notes"] == ["Dry run: nothing was written."]


async def test_stale_page_is_refused(make_server: MakeServer, fake_bookstack: PageStore) -> None:
    fake_bookstack.page(7, markdown="text", updated_at=LATER)
    result = await call(
        make_server(tools=[append_to_page]), page_id=7, content="more", expected_updated_at=UPDATED
    )
    assert result.is_error
    assert LATER in result.content[0].text and UPDATED in result.content[0].text
    assert fake_bookstack.puts == []


async def test_changelog_is_not_sent_before_26_09(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="text")
    result = await call(
        make_server(tools=[append_to_page], version="v26.05.5"), page_id=7, content="more"
    )
    assert fake_bookstack.puts == [{"markdown": "text\n\nmore"}]
    assert any("changelog not sent" in n for n in result.structured_content["notes"])


async def test_unstored_append_is_reported_unverified(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="text")
    fake_bookstack.ignore_puts = True
    result = await call(make_server(tools=[append_to_page]), page_id=7, content="more")
    data = result.structured_content
    assert (data["written"], data["verified"], data["unverified_count"]) == (True, False, 1)


async def test_long_content_is_cut_in_the_context(
    make_server: MakeServer, fake_bookstack: PageStore
) -> None:
    fake_bookstack.page(7, markdown="start")
    content = "a" * 200 + "MIDDLE" + "b" * 200
    result = await call(make_server(tools=[append_to_page]), page_id=7, content=content)
    context = result.structured_content["contexts"][0]["context"]
    # The separator counts towards the 240 characters shown of the inserted text.
    assert context == "start\n\n" + "a" * 118 + "…" + "b" * 120


async def test_annotations_and_read_only_mode(make_server: MakeServer) -> None:
    async with Client(make_server(tools=[append_to_page])) as mcp:
        [listed] = await mcp.list_tools()
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is False
    assert listed.annotations.destructive_hint is False
    assert listed.annotations.idempotent_hint is False
    assert "deps" not in listed.input_schema["properties"]
    async with Client(make_server(tools=[append_to_page], read_only=True)) as mcp:
        assert await mcp.list_tools() == []
