"""edit_page against a real BookStack: what is stored, verification and the guards."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.edit_page import edit_page

from .conftest import BookStackInstance, _expect, api

pytestmark = pytest.mark.integration


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
        yield build_server(settings, client, system, tools=[edit_page])


def create_page(admin_client: httpx2.Client, book_id: int, **content: str) -> dict[str, Any]:
    body = {"book_id": book_id, "name": "Edit target", **content}
    page: dict[str, Any] = _expect(api(admin_client, "POST", "/pages", json=body), 200)
    return page


def read_page(admin_client: httpx2.Client, page_id: int) -> dict[str, Any]:
    page: dict[str, Any] = _expect(api(admin_client, "GET", f"/pages/{page_id}"), 200)
    return page


async def edit(server: FastMCP[Any], **args: Any) -> Any:
    async with Client(server) as mcp:
        return await mcp.call_tool("edit_page", args, raise_on_error=False)


async def test_markdown_edit_is_verified_and_adds_one_revision(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="# Title\n\nHello world.\n")
    before = read_page(admin_client, page["id"])

    result = await edit(
        server,
        page_id=page["id"],
        edits=[{"old_string": "Hello world.", "new_string": "Hello there."}],
        expected_updated_at=before["updated_at"],
    )

    assert not result.is_error, result.content
    data = result.structured_content
    assert data["verified"] is True
    assert data["revision_count"] == before["revision_count"] + 1
    after = read_page(admin_client, page["id"])
    assert "Hello there." in after["markdown"]
    assert after["revision_count"] == before["revision_count"] + 1


async def test_html_edit_verifies_despite_bookstack_ids(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, html="<h2>Setup</h2>\n<p>Install the tool.</p>")
    stored = read_page(admin_client, page["id"])["raw_html"]
    assert "bkmrk-" in stored
    old = "Install the tool."
    assert old in stored

    result = await edit(
        server, page_id=page["id"], edits=[{"old_string": old, "new_string": "Install it."}]
    )

    assert not result.is_error, result.content
    assert result.structured_content["source_field"] == "html"
    assert result.structured_content["verified"] is True
    assert "Install it." in read_page(admin_client, page["id"])["raw_html"]


async def test_page_include_tag_survives_an_edit(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    other = create_page(admin_client, sandbox_book, markdown="Included text.")
    include = f"{{{{@{other['id']}}}}}"
    page = create_page(admin_client, sandbox_book, markdown=f"Intro line.\n\n{include}\n\nOutro.")

    result = await edit(
        server, page_id=page["id"], edits=[{"old_string": "Intro line.", "new_string": "Intro."}]
    )

    assert not result.is_error, result.content
    stored = read_page(admin_client, page["id"])["markdown"]
    assert include in stored
    assert "Included text." not in stored


async def test_markdown_page_with_wysiwyg_editor_is_edited_as_markdown(
    server: FastMCP[Any],
    admin_client: httpx2.Client,
    sandbox_book: int,
    tinker: Callable[[str], str],
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="Some **bold** text.")
    tinker(
        f"\\BookStack\\Entities\\Models\\Page::query()->where('id', {page['id']})"
        "->update(['editor' => 'wysiwyg']);"
    )
    before = read_page(admin_client, page["id"])
    assert before["editor"] == "wysiwyg"
    assert before["markdown"]

    result = await edit(
        server, page_id=page["id"], edits=[{"old_string": "**bold**", "new_string": "*bold*"}]
    )

    assert not result.is_error, result.content
    assert result.structured_content["source_field"] == "markdown"
    assert result.structured_content["verified"] is True
    assert read_page(admin_client, page["id"])["markdown"] == "Some *bold* text."


async def test_dry_run_leaves_the_page_unchanged(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="old text")
    before = read_page(admin_client, page["id"])

    result = await edit(
        server,
        page_id=page["id"],
        edits=[{"old_string": "old", "new_string": "new"}],
        dry_run=True,
    )

    assert result.structured_content["written"] is False
    after = read_page(admin_client, page["id"])
    assert after["updated_at"] == before["updated_at"]
    assert after["markdown"] == "old text"


async def test_stale_expected_updated_at_is_refused(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    page = create_page(admin_client, sandbox_book, markdown="old text")
    stale = "2000-01-01T00:00:00.000000Z"

    result = await edit(
        server,
        page_id=page["id"],
        edits=[{"old_string": "old", "new_string": "new"}],
        expected_updated_at=stale,
    )

    assert result.is_error
    assert stale in result.content[0].text
    assert read_page(admin_client, page["id"])["markdown"] == "old text"


async def test_ambiguous_anchor_is_refused_with_three_contexts(
    server: FastMCP[Any], admin_client: httpx2.Client, sandbox_book: int
) -> None:
    markdown = "\n\n".join(f"Step {n}: TODO" for n in range(4))
    page = create_page(admin_client, sandbox_book, markdown=markdown)

    result = await edit(
        server, page_id=page["id"], edits=[{"old_string": "TODO", "new_string": "done"}]
    )

    assert result.is_error
    message = result.content[0].text
    assert "occurs 4 times" in message
    assert all(f"{n}. " in message for n in (1, 2, 3))
    assert "4. " not in message
    assert read_page(admin_client, page["id"])["markdown"] == markdown
