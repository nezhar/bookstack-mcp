"""The `search` tool against a real BookStack."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
from fastmcp import Client

from bookstack_mcp.client import BookStackClient
from bookstack_mcp.config import Settings
from bookstack_mcp.server import build_server
from bookstack_mcp.tools.search import search

from .conftest import BookStackInstance, Credentials, api

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _mcp(
    bookstack: BookStackInstance, credentials: Credentials
) -> AsyncIterator[Client[Any]]:
    async with BookStackClient(
        bookstack.base_url, credentials.token_id, credentials.token_secret
    ) as client:
        settings = Settings(
            url=bookstack.base_url,
            token_id=credentials.token_id,
            token_secret=credentials.token_secret,
            _env_file=None,
        )
        server = build_server(settings, client, await client.system(), tools=[search])
        async with Client(server) as mcp:
            yield mcp


@dataclass(frozen=True)
class Corpus:
    book_id: int
    word: str
    draft_id: int
    plain_id: int
    scoped_search: bool


def _create_page(admin: httpx2.Client, book_id: int, name: str, **extra: Any) -> int:
    response = api(admin, "POST", "/pages", json={"book_id": book_id, "name": name, **extra})
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


@pytest.fixture
def corpus(admin_client: httpx2.Client, sandbox_book: int) -> Corpus:
    """Two pages sharing a unique word, one tagged status=draft."""
    word = f"zq{uuid.uuid4().hex[:12]}"
    draft_id = _create_page(
        admin_client,
        sandbox_book,
        f"Draft {word}",
        markdown=f"The {word} draft page.",
        tags=[{"name": "status", "value": "draft"}],
    )
    plain_id = _create_page(
        admin_client, sandbox_book, f"Plain {word}", markdown=f"The {word} plain page."
    )
    version = api(admin_client, "GET", "/system").json()["version"]
    major, minor = (int(p) for p in version.lstrip("v").split(".")[:2])
    return Corpus(sandbox_book, word, draft_id, plain_id, (major, minor) >= (26, 9))


async def _search(mcp: Client[Any], **args: Any) -> dict[str, Any]:
    result = await mcp.call_tool("search", args)
    assert result.structured_content is not None
    return dict(result.structured_content)


async def test_tag_filter_finds_the_tagged_page(
    bookstack: BookStackInstance, corpus: Corpus
) -> None:
    async with _mcp(bookstack, bookstack.admin) as mcp:
        data = await _search(mcp, query=f"{corpus.word} [status=draft]")
    assert [r["id"] for r in data["results"]] == [corpus.draft_id]
    [hit] = data["results"]
    assert hit["type"] == "page"
    assert {"name": "status", "value": "draft"} in hit["tags"]
    assert hit["book"]["id"] == corpus.book_id
    assert hit["url"]
    assert "<" not in hit["preview"]


async def test_type_filter_and_word(bookstack: BookStackInstance, corpus: Corpus) -> None:
    async with _mcp(bookstack, bookstack.admin) as mcp:
        data = await _search(mcp, query=f"{{type:page}} {corpus.word} -draft")
    assert [r["id"] for r in data["results"]] == [corpus.plain_id]
    assert data["total"] == 1
    assert data["has_more"] is False


async def test_book_scope(
    bookstack: BookStackInstance, admin_client: httpx2.Client, corpus: Corpus
) -> None:
    other = api(admin_client, "POST", "/books", json={"name": f"it-{uuid.uuid4().hex}"}).json()
    try:
        outside = _create_page(
            admin_client, other["id"], f"Outside {corpus.word}", markdown=corpus.word
        )
        async with _mcp(bookstack, bookstack.admin) as mcp:
            if not corpus.scoped_search:
                result = await mcp.call_tool(
                    "search",
                    {"query": corpus.word, "book_id": corpus.book_id},
                    raise_on_error=False,
                )
                assert result.is_error
                assert "26.09" in result.content[0].text  # type: ignore[union-attr]
                return
            everywhere = await _search(mcp, query=corpus.word)
            scoped = await _search(mcp, query=corpus.word, book_id=corpus.book_id)
        assert outside in {r["id"] for r in everywhere["results"]}
        ids = {r["id"] for r in scoped["results"]}
        assert {corpus.draft_id, corpus.plain_id} <= ids
        assert outside not in ids
    finally:
        api(admin_client, "DELETE", f"/books/{other['id']}")


async def test_viewer_gets_results(bookstack: BookStackInstance, corpus: Corpus) -> None:
    async with _mcp(bookstack, bookstack.viewer) as mcp:
        data = await _search(mcp, query=corpus.word)
    assert {corpus.draft_id, corpus.plain_id} <= {r["id"] for r in data["results"]}
