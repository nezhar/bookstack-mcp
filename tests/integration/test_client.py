"""BookStackClient against a real BookStack."""

import uuid
from collections.abc import AsyncIterator

import pytest

from bookstack_mcp.client import (
    BookStackClient,
    BookStackForbidden,
    BookStackNotFound,
    BookStackValidationError,
    parse_version,
)

from .conftest import BookStackInstance, Credentials

pytestmark = pytest.mark.integration


def _client(bookstack: BookStackInstance, credentials: Credentials) -> BookStackClient:
    return BookStackClient(bookstack.base_url, credentials.token_id, credentials.token_secret)


@pytest.fixture
async def admin(bookstack: BookStackInstance) -> AsyncIterator[BookStackClient]:
    async with _client(bookstack, bookstack.admin) as client:
        yield client


@pytest.fixture
async def viewer(bookstack: BookStackInstance) -> AsyncIterator[BookStackClient]:
    async with _client(bookstack, bookstack.viewer) as client:
        yield client


async def test_draft_filter_is_sent_as_0_and_1(admin: BookStackClient, sandbox_book: int) -> None:
    page = await admin.post_json(
        "/pages", {"book_id": sandbox_book, "name": "Client page", "markdown": "# Hi"}
    )

    drafts = await admin.list_page("/pages", filters={"book_id": sandbox_book, "draft": True})
    assert drafts.data == []
    assert drafts.total == 0

    published = await admin.list_page("/pages", filters={"book_id": sandbox_book, "draft": False})
    assert [p["id"] for p in published.data] == [page["id"]]
    assert published.total == 1


async def test_name_like_finds_book_by_fragment(admin: BookStackClient, sandbox_book: int) -> None:
    book = await admin.get_json(f"/books/{sandbox_book}")
    fragment = book["name"][3:15]  # inside the uuid hex of "it-<hex>"
    found = await admin.list_page("/books", filters={"name:like": f"%{fragment}%"})
    assert sandbox_book in [b["id"] for b in found.data]


async def test_missing_page_is_not_found(admin: BookStackClient) -> None:
    with pytest.raises(BookStackNotFound):
        await admin.get_json("/pages/999999999")


async def test_page_without_name_is_a_validation_error(
    admin: BookStackClient, sandbox_book: int
) -> None:
    with pytest.raises(BookStackValidationError) as info:
        await admin.post_json("/pages", {"book_id": sandbox_book, "markdown": "no name"})
    assert "name" in info.value.field_errors
    assert info.value.field_errors["name"]


async def test_viewer_cannot_create(viewer: BookStackClient) -> None:
    with pytest.raises(BookStackForbidden) as info:
        await viewer.post_json("/books", {"name": f"it-viewer-{uuid.uuid4().hex}"})
    assert info.value.message


async def test_system_returns_a_version(admin: BookStackClient, viewer: BookStackClient) -> None:
    info = await admin.system()
    assert parse_version(info.version) >= (26, 5, 0)
    assert info.base_url
    assert info.links.page(1) == f"{info.base_url.rstrip('/')}/link/1"
    # Any user with API access may call it.
    assert (await viewer.system()).version == info.version
