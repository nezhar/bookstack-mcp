"""read_page against a real BookStack."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.read_page import HTML_ANCHOR_NOTE, read_page

from .conftest import BookStackInstance, _expect, api

pytestmark = pytest.mark.integration

MARKDOWN = (
    "Lead paragraph.\n\n# Setup\n\nInstall it.\n\n## Linux\n\nUse apt.\n\n# Usage\n\nRun it.\n"
)
HTML = (
    "<p>Lead &amp; paragraph.</p>"
    "<h2>Setup</h2><p>Install <strong>the tool</strong> first.</p>"
    "<h2>Usage</h2><p>Run it.</p>"
)


@pytest.fixture
async def mcp(bookstack: BookStackInstance) -> AsyncIterator[Client[Any]]:
    admin = bookstack.admin
    async with BookStackClient(bookstack.base_url, admin.token_id, admin.token_secret) as client:
        settings = Settings(
            url=bookstack.base_url,
            token_id=admin.token_id,
            token_secret=admin.token_secret,
            _env_file=None,
        )
        server = build_server(settings, client, await client.system(), tools=[read_page])
        async with Client(server) as mcp_client:
            yield mcp_client


def create_page(admin_client: httpx2.Client, book_id: int, **fields: Any) -> dict[str, Any]:
    body = {"book_id": book_id, **fields}
    page: dict[str, Any] = _expect(api(admin_client, "POST", "/pages", json=body), 200)
    return page


async def read(mcp: Client[Any], **args: Any) -> dict[str, Any]:
    result = await mcp.call_tool("read_page", args)
    assert result.structured_content is not None
    return result.structured_content


async def test_markdown_page(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, name="MD page", markdown=MARKDOWN)

    out = await read(mcp, page_id=page["id"])

    assert out["source_field"] == "markdown"
    assert out["content"] == MARKDOWN
    assert out["book_id"] == sandbox_book
    assert out["updated_at"] == page["updated_at"]
    assert out["url"].endswith(f"/link/{page['id']}")
    assert out["notes"] == []


async def test_html_page_markdown_view(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, name="HTML page", html=HTML)

    out = await read(mcp, page_id=page["id"])

    assert out["source_field"] == "html"
    assert not out["content"].startswith("# HTML page")
    assert "## Setup" in out["content"]
    assert "**the tool**" in out["content"]
    assert HTML_ANCHOR_NOTE in out["notes"]


async def test_outline_offsets_point_at_headings(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    md = create_page(admin_client, sandbox_book, name="MD", markdown=MARKDOWN)
    html = create_page(admin_client, sandbox_book, name="HTML", html=HTML)
    raw_html = _expect(api(admin_client, "GET", f"/pages/{html['id']}"), 200)["raw_html"]

    md_outline = (await read(mcp, page_id=md["id"], format="outline"))["outline"]
    html_outline = (await read(mcp, page_id=html["id"], format="outline"))["outline"]

    assert [h["text"] for h in md_outline] == ["Setup", "Linux", "Usage"]
    for heading in md_outline:
        assert MARKDOWN[heading["offset"] :].lstrip("#").startswith(" " + heading["text"])
    assert [h["text"] for h in html_outline] == ["Setup", "Usage"]
    for heading in html_outline:
        assert raw_html[heading["offset"] :].lower().startswith("<h2")
        assert heading["anchor"]


async def test_long_page_reads_in_three_chunks(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    paragraph = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 3 + "\n\n"
    text = (paragraph * (50_000 // len(paragraph) + 1))[:50_000]
    page = create_page(admin_client, sandbox_book, name="Long", markdown=text)
    stored = _expect(api(admin_client, "GET", f"/pages/{page['id']}"), 200)["markdown"]

    parts: list[str] = []
    offset = 0
    while True:
        out = await read(mcp, page_id=page["id"], format="source", offset=offset)
        parts.append(out["content"])
        if not out["truncated"]:
            break
        offset = out["next_offset"]

    assert len(parts) == 3
    assert "".join(parts) == stored
    assert out["total_chars"] == len(stored)


async def test_grep_excerpts_appear_in_raw_html(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, name="HTML", html=HTML)
    raw_html = _expect(api(admin_client, "GET", f"/pages/{page['id']}"), 200)["raw_html"]

    out = await read(mcp, page_id=page["id"], grep="the tool", context=20)

    assert out["grep"]["total"] == 1
    for match in out["grep"]["matches"]:
        assert match["excerpt"].strip("…") in raw_html
        assert raw_html[match["offset"] :].startswith(match["match"])


async def test_include_tag_survives_in_source(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    target = create_page(admin_client, sandbox_book, name="Target", html=HTML)
    tag = f"{{{{@{target['id']}#bkmrk-setup}}}}"
    html_page = create_page(admin_client, sandbox_book, name="Includer", html=f"<p>{tag}</p>")
    md_page = create_page(admin_client, sandbox_book, name="MD includer", markdown=f"{tag}\n")

    html_out = await read(mcp, page_id=html_page["id"], format="source")
    md_out = await read(mcp, page_id=md_page["id"], format="source")
    md_view = await read(mcp, page_id=md_page["id"])

    assert tag in html_out["content"]
    assert tag in md_out["content"]
    assert tag in md_view["content"]


async def test_unknown_section_lists_headings(
    mcp: Client[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, name="MD", markdown=MARKDOWN)

    result = await mcp.call_tool(
        "read_page", {"page_id": page["id"], "section": "Missing"}, raise_on_error=False
    )

    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    for heading in ("Setup", "Linux", "Usage"):
        assert repr(heading) in text
