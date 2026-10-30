"""append_to_page against a real BookStack: where the content lands and what is stored."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.append_to_page import append_to_page

from .conftest import BookStackInstance, _expect, api

pytestmark = pytest.mark.integration

OUTLINE_PAGE = (
    "# Guide\n\nIntro.\n\n## Setup\n\nInstall.\n\n### Linux\n\nUse apt.\n\n## Usage\n\nRun it.\n"
)


@pytest.fixture
async def server(bookstack: BookStackInstance) -> AsyncIterator[FastMCP[Any]]:
    creds = bookstack.admin
    settings = Settings(
        url=bookstack.base_url,
        token_id=creds.token_id,
        token_secret=creds.token_secret,
        _env_file=None,
    )
    async with BookStackClient(bookstack.base_url, creds.token_id, creds.token_secret) as client:
        system = await client.system()
        yield build_server(settings, client, system, tools=[append_to_page])


def create_page(admin_client: httpx2.Client, book_id: int, **content: str) -> dict[str, Any]:
    body = {"book_id": book_id, "name": "Append target", **content}
    page: dict[str, Any] = _expect(api(admin_client, "POST", "/pages", json=body), 200)
    return page


def read_page(admin_client: httpx2.Client, page_id: int) -> dict[str, Any]:
    page: dict[str, Any] = _expect(api(admin_client, "GET", f"/pages/{page_id}"), 200)
    return page


async def append(server: FastMCP[Any], **args: Any) -> Any:
    async with Client(server) as mcp:
        return await mcp.call_tool("append_to_page", args, raise_on_error=False)


async def test_append_to_the_end_of_a_markdown_page(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="# Title\n\nHello.\n")
    before = read_page(admin_client, page["id"])

    result = await append(
        server,
        page_id=page["id"],
        content="Appended **line**.",
        expected_updated_at=before["updated_at"],
    )

    assert not result.is_error, result.content
    data = result.structured_content
    assert data["verified"] is True
    assert data["position"] == "end"
    assert data["revision_count"] == before["revision_count"] + 1
    after = read_page(admin_client, page["id"])
    assert after["markdown"].rstrip().endswith("Hello.\n\nAppended **line**.")
    assert after["revision_count"] == before["revision_count"] + 1


async def test_section_end_lands_after_the_subsection(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown=OUTLINE_PAGE)

    result = await append(
        server,
        page_id=page["id"],
        content="Windows: use the installer.",
        position="section_end",
        section="Setup",
    )

    assert not result.is_error, result.content
    assert result.structured_content["verified"] is True
    stored = read_page(admin_client, page["id"])["markdown"]
    assert "Use apt.\n\nWindows: use the installer.\n\n## Usage" in stored


async def test_after_heading_lands_right_below_the_heading(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown=OUTLINE_PAGE)

    result = await append(
        server,
        page_id=page["id"],
        content="Read this first.",
        position="after_heading",
        section="Setup",
    )

    assert not result.is_error, result.content
    assert result.structured_content["verified"] is True
    stored = read_page(admin_client, page["id"])["markdown"]
    assert "## Setup\n\nRead this first.\n\nInstall." in stored


async def test_plain_text_on_an_html_page_arrives_as_a_paragraph(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, html="<h2>Notes</h2>\n<p>First.</p>")

    result = await append(server, page_id=page["id"], content="Second & last.")

    assert not result.is_error, result.content
    data = result.structured_content
    assert data["source_field"] == "html"
    assert data["verified"] is True
    stored = read_page(admin_client, page["id"])["raw_html"]
    assert re.search(r"<p[^>]*>Second &amp; last\.</p>", stored), stored
    assert stored.index("First.") < stored.index("Second &amp; last.")


async def test_unknown_section_lists_the_headings(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown=OUTLINE_PAGE)
    before = read_page(admin_client, page["id"])

    result = await append(
        server, page_id=page["id"], content="x", position="section_end", section="Deploy"
    )

    assert result.is_error
    message = result.content[0].text
    assert all(f"'{h}'" in message for h in ("Guide", "Setup", "Linux", "Usage"))
    assert 'format="outline"' in message
    after = read_page(admin_client, page["id"])
    assert after["updated_at"] == before["updated_at"]
    assert after["markdown"] == before["markdown"]


async def test_dry_run_writes_nothing(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="Some text.")
    before = read_page(admin_client, page["id"])

    result = await append(server, page_id=page["id"], content="More text.", dry_run=True)

    assert not result.is_error, result.content
    data = result.structured_content
    assert (data["dry_run"], data["written"]) == (True, False)
    assert "+More text." in data["diff"]
    after = read_page(admin_client, page["id"])
    assert after["updated_at"] == before["updated_at"]
    assert after["revision_count"] == before["revision_count"]
    assert after["markdown"] == "Some text."
