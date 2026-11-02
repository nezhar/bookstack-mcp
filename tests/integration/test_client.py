"""BookStackClient against a real BookStack."""

import asyncio
import struct
import uuid
import zlib
from collections.abc import AsyncIterator

import pytest

from bookstack_mcp.client import (
    BookStackClient,
    BookStackForbidden,
    BookStackNotFound,
    BookStackResponseTooLarge,
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


def _png() -> bytes:
    """A valid 1x1 PNG, built by hand so the tests need no image library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


async def test_json_write_update_and_delete(admin: BookStackClient, sandbox_book: int) -> None:
    page = await admin.post_json(
        "/pages", {"book_id": sandbox_book, "name": "Client write", "markdown": "# Hi\n\nBody"}
    )
    renamed = await admin.put_json(f"/pages/{page['id']}", {"name": "Client renamed"})
    assert renamed["name"] == "Client renamed"
    listed = await admin.list_page("/pages", filters={"book_id": sandbox_book})
    assert [p["id"] for p in listed.data] == [page["id"]]
    assert listed.total == 1
    await admin.delete(f"/pages/{page['id']}")
    with pytest.raises(BookStackNotFound):
        await admin.get_json(f"/pages/{page['id']}")


async def test_text_and_binary_exports(admin: BookStackClient, sandbox_book: int) -> None:
    page = await admin.post_json(
        "/pages", {"book_id": sandbox_book, "name": "Export me", "markdown": "Café body"}
    )
    markdown = await admin.get_text(f"/pages/{page['id']}/export/markdown")
    assert "Café body" in markdown
    plain = await admin.get_text(f"/pages/{page['id']}/export/plaintext")
    assert "Café body" in plain
    pdf = await admin.get_bytes(f"/pages/{page['id']}/export/pdf", max_bytes=10_000_000)
    assert pdf.startswith(b"%PDF")
    with pytest.raises(BookStackResponseTooLarge):
        await admin.get_bytes(f"/pages/{page['id']}/export/pdf", max_bytes=100)


async def test_multipart_upload_and_put_override(admin: BookStackClient, sandbox_book: int) -> None:
    page = await admin.post_json(
        "/pages", {"book_id": sandbox_book, "name": "With image", "markdown": "x"}
    )
    image = await admin.post_multipart(
        "/image-gallery",
        {"type": "gallery", "uploaded_to": page["id"], "name": "pixel"},
        {"image": ("pixel.png", _png(), "image/png")},
    )
    assert image["name"] == "pixel"
    assert image["uploaded_to"] == page["id"]
    # A file update goes as POST with _method=PUT; BookStack then treats it as an update.
    book = await admin.post_multipart(
        f"/books/{sandbox_book}",
        {"name": "Book with cover"},
        {"image": ("cover.png", _png(), "image/png")},
        method_override="PUT",
    )
    assert book["name"] == "Book with cover"
    assert book["cover"] is not None


async def test_twenty_quick_reads_all_succeed(bookstack: BookStackInstance) -> None:
    # Past the burst of 10, the limiter paces the rest instead of letting BookStack throttle.
    async with BookStackClient(
        bookstack.base_url,
        bookstack.admin.token_id,
        bookstack.admin.token_secret,
        requests_per_minute=150,
    ) as client:
        results = await asyncio.gather(
            *(client.get_json("/books", {"count": 1}) for _ in range(20))
        )
    assert all("data" in r for r in results)
