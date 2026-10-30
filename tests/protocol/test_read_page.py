"""read_page through MCP: formats, sections, grep, chunking and error mapping.

BookStack is faked here; what BookStack returns is proven in tests/integration.
"""

from __future__ import annotations

from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.tools.read_page import HTML_ANCHOR_NOTE, read_page

from .conftest import FakeBookStack, MakeServer

MARKDOWN = "Intro text\n\n# Setup\n\nInstall it.\n\n## Linux\n\nUse apt.\n\n# Usage\n\nRun it.\n"
RAW_HTML = (
    '<p id="bkmrk-a">Lead &amp; more</p>\n'
    '<h2 id="bkmrk-setup">Setup</h2>\n<p id="bkmrk-b">Install <strong>it</strong>.</p>\n'
    '<h2 id="bkmrk-usage">Usage</h2>\n<p id="bkmrk-c">{{@7#bkmrk-x}}</p>\n'
)
EXPORT = "# HTML page\n\nLead & more\n\n## Setup\n\nInstall **it**.\n\n## Usage\n\n{{@7#bkmrk-x}}\n"


class TextFakeBookStack(FakeBookStack):
    """Adds plain-text routes, for exports."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: dict[tuple[str, str], str] = {}

    def add_text(self, method: str, path: str, body: str) -> None:
        self.texts[(method.upper(), path)] = body

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        key = (request.method, request.url.path)
        if key in self.texts:
            self.requests.append(request)
            return httpx2.Response(
                200, text=self.texts[key], headers={"Content-Type": "text/plain; charset=utf-8"}
            )
        return super().handler(request)


@pytest.fixture
def fake_bookstack() -> TextFakeBookStack:
    return TextFakeBookStack()


def page(page_id: int, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": page_id,
        "name": "Page",
        "book_id": 3,
        "chapter_id": 0,
        "editor": "markdown",
        "markdown": "",
        "raw_html": "",
        "html": "<p>rendered</p>",
        "updated_at": "2026-10-01T10:00:00.000000Z",
        "revision_count": 4,
        "tags": [{"name": "team", "value": "ops", "order": 0}],
        "comments": {"active": [{"html": "<p>a comment</p>"}], "archived": []},
    }
    body.update(fields)
    return body


@pytest.fixture
def pages(fake_bookstack: TextFakeBookStack) -> TextFakeBookStack:
    fake_bookstack.add("GET", "/api/pages/1", page(1, name="MD page", markdown=MARKDOWN))
    fake_bookstack.add(
        "GET",
        "/api/pages/2",
        page(2, name="HTML page", editor="wysiwyg", raw_html=RAW_HTML, chapter_id=9),
    )
    fake_bookstack.add_text("GET", "/api/pages/2/export/markdown", EXPORT)
    return fake_bookstack


async def call(server: Any, **args: Any) -> dict[str, Any]:
    async with Client(server) as mcp:
        result = await mcp.call_tool("read_page", args)
    assert result.structured_content is not None
    return result.structured_content


async def test_markdown_page_metadata_and_content(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=1)
    assert out["page_id"] == 1
    assert out["name"] == "MD page"
    assert out["url"] == "https://docs.example.com/link/1"
    assert out["book_id"] == 3
    assert out["chapter_id"] is None
    assert out["tags"] == [{"name": "team", "value": "ops"}]
    assert out["editor"] == "markdown"
    assert out["source_field"] == "markdown"
    assert out["updated_at"] == "2026-10-01T10:00:00.000000Z"
    assert out["revision_count"] == 4
    assert out["content"] == MARKDOWN
    assert out["total_chars"] == out["returned_chars"] == len(MARKDOWN)
    assert out["offset"] == 0
    assert out["next_offset"] is None
    assert out["truncated"] is False
    assert out["notes"] == []
    assert "comment" not in str(out)
    assert [r.url.path for r in pages.requests] == ["/api/pages/1"]


async def test_html_page_markdown_view_uses_the_export(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=2)
    assert out["source_field"] == "html"
    assert out["chapter_id"] == 9
    assert out["content"] == EXPORT.removeprefix("# HTML page\n\n")
    assert "{{@7#bkmrk-x}}" in out["content"]
    assert HTML_ANCHOR_NOTE in out["notes"]


async def test_source_returns_raw_html(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=2, format="source")
    assert out["content"] == RAW_HTML
    assert out["notes"] == []
    assert [r.url.path for r in pages.requests] == ["/api/pages/2"]


async def test_outline_has_source_offsets_and_no_content(
    make_server: MakeServer, pages: Any
) -> None:
    out = await call(make_server(tools=[read_page]), page_id=2, format="outline")
    assert out["content"] == ""
    assert [(h["level"], h["text"], h["anchor"]) for h in out["outline"]] == [
        (2, "Setup", "bkmrk-setup"),
        (2, "Usage", "bkmrk-usage"),
    ]
    for heading in out["outline"]:
        assert RAW_HTML[heading["offset"] :].startswith("<h2")


async def test_section_of_markdown(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=1, section="setup")
    assert out["section"] == "Setup"
    assert out["content"] == "# Setup\n\nInstall it.\n\n## Linux\n\nUse apt.\n\n"


async def test_section_of_html_markdown_view(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=2, section="Usage")
    assert out["content"] == "## Usage\n\n{{@7#bkmrk-x}}\n"


async def test_section_of_outline(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=1, format="outline", section="Setup")
    assert [h["text"] for h in out["outline"]] == ["Setup", "Linux"]
    assert MARKDOWN[out["outline"][1]["offset"] :].startswith("## Linux")


async def test_unknown_section_lists_headings(make_server: MakeServer, pages: Any) -> None:
    async with Client(make_server(tools=[read_page])) as mcp:
        result = await mcp.call_tool(
            "read_page", {"page_id": 1, "section": "Nope"}, raise_on_error=False
        )
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "'Setup'" in text and "'Linux'" in text and "'Usage'" in text


async def test_grep_excerpts_come_from_the_stored_source(
    make_server: MakeServer, pages: Any
) -> None:
    out = await call(make_server(tools=[read_page]), page_id=2, grep="install", context=5)
    assert out["content"] == ""
    assert out["grep"]["total"] == 1
    assert out["grep"]["truncated"] is False
    [match] = out["grep"]["matches"]
    assert match["match"] == "Install"
    assert RAW_HTML[match["offset"] :].startswith("Install <strong>")
    assert match["excerpt"].strip("…") in RAW_HTML
    assert [r.url.path for r in pages.requests] == ["/api/pages/2"]


async def test_grep_within_section_reports_absolute_offsets(
    make_server: MakeServer, pages: Any
) -> None:
    out = await call(
        make_server(tools=[read_page]), page_id=1, grep="it", section="Usage", case_sensitive=True
    )
    [match] = out["grep"]["matches"]
    assert MARKDOWN[match["offset"] :].startswith("it.\n")
    assert match["offset"] > MARKDOWN.index("# Usage")


async def test_grep_truncation(make_server: MakeServer, pages: Any) -> None:
    out = await call(make_server(tools=[read_page]), page_id=1, grep="t", max_matches=1)
    assert len(out["grep"]["matches"]) == 1
    assert out["grep"]["total"] > 1
    assert out["grep"]["truncated"] is True


async def test_chunks_join_exactly(make_server: MakeServer, fake_bookstack: Any) -> None:
    text = "".join(f"Paragraph {i} " + "x" * 80 + "\n\n" for i in range(60))
    fake_bookstack.add("GET", "/api/pages/5", page(5, markdown=text))
    server = make_server(tools=[read_page])
    parts, offset = [], 0
    while True:
        out = await call(server, page_id=5, offset=offset, max_chars=1000)
        parts.append(out["content"])
        assert out["total_chars"] == len(text)
        if not out["truncated"]:
            assert out["next_offset"] is None
            break
        assert f"offset={out['next_offset']}" in " ".join(out["notes"])
        offset = out["next_offset"]
    assert "".join(parts) == text
    assert len(parts) > 1


async def test_default_max_chars_comes_from_settings(
    make_server: MakeServer, fake_bookstack: Any
) -> None:
    fake_bookstack.add("GET", "/api/pages/5", page(5, markdown="y" * 3000))
    out = await call(make_server(tools=[read_page], max_read_chars=1000), page_id=5)
    assert out["returned_chars"] == 1000
    assert out["next_offset"] == 1000


async def test_offset_past_end_is_a_tool_error(make_server: MakeServer, pages: Any) -> None:
    async with Client(make_server(tools=[read_page])) as mcp:
        result = await mcp.call_tool(
            "read_page", {"page_id": 1, "offset": 10_000}, raise_on_error=False
        )
    assert result.is_error
    assert "offset must be between" in result.content[0].text  # type: ignore[union-attr]


async def test_missing_page_maps_to_tool_error(make_server: MakeServer, pages: Any) -> None:
    async with Client(make_server(tools=[read_page])) as mcp:
        result = await mcp.call_tool("read_page", {"page_id": 99}, raise_on_error=False)
    assert result.is_error
    assert "HTTP 404" in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "args",
    [
        {"grep": ""},
        {"grep": "x" * 1001},
        {"max_chars": 100_001},
        {"format": "html"},
        {"max_matches": 51},
    ],
)
async def test_invalid_inputs_are_rejected(
    make_server: MakeServer, pages: Any, args: dict[str, Any]
) -> None:
    async with Client(make_server(tools=[read_page])) as mcp:
        result = await mcp.call_tool("read_page", {"page_id": 1, **args}, raise_on_error=False)
    assert result.is_error


async def test_read_page_is_read_only_and_visible_in_read_only_mode(
    make_server: MakeServer,
) -> None:
    async with Client(make_server(tools=[read_page], read_only=True)) as mcp:
        [listed] = [t for t in await mcp.list_tools() if t.name == "read_page"]
    assert listed.annotations is not None
    assert listed.annotations.read_only_hint is True
