"""Request building of BookStackClient, through httpx2.MockTransport.

These tests check what the client sends, never how BookStack answers.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from bookstack_mcp import __version__
from bookstack_mcp.client import BookStackClient, BookStackResponseTooLarge, ListPage
from bookstack_mcp.client.http import list_params

Handler = Callable[[httpx2.Request], httpx2.Response]


def make_client(handler: Handler, **kwargs: Any) -> BookStackClient:
    return BookStackClient(
        "https://docs.example.com/",
        "tid",
        SecretStr("tsecret"),
        transport=httpx2.MockTransport(handler),
        **kwargs,
    )


class Recorder:
    def __init__(self, response: httpx2.Response | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.response = response or httpx2.Response(200, json={"ok": True})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        self.requests.append(request)
        return self.response

    @property
    def last(self) -> httpx2.Request:
        return self.requests[-1]


async def test_headers_and_url() -> None:
    rec = Recorder()
    async with make_client(rec) as client:
        assert await client.get_json("/pages/12") == {"ok": True}
    req = rec.last
    assert str(req.url) == "https://docs.example.com/api/pages/12"
    assert req.headers["Authorization"] == "Token tid:tsecret"
    assert req.headers["Accept"] == "application/json"
    assert req.headers["User-Agent"] == f"bookstack-mcp/{__version__}"


async def test_plain_string_secret_and_custom_user_agent() -> None:
    rec = Recorder()
    client = BookStackClient(
        "https://h", "a", "b", user_agent="x/1", transport=httpx2.MockTransport(rec)
    )
    await client.get_json("pages")
    await client.aclose()
    assert rec.last.url.path == "/api/pages"
    assert rec.last.headers["Authorization"] == "Token a:b"
    assert rec.last.headers["User-Agent"] == "x/1"


def test_repr_hides_token() -> None:
    client = make_client(Recorder())
    assert "tsecret" not in repr(client)


def test_filter_encoding() -> None:
    params = list_params(filters={"draft": False, "book_id": 3, "name:like": "%intro%"})
    assert params == {
        "filter[draft]": "0",
        "filter[book_id]": "3",
        "filter[name:like]": "%intro%",
    }
    assert list_params(filters={"template": True}) == {"filter[template]": "1"}


def test_count_clamped() -> None:
    assert list_params(count=1000)["count"] == "500"
    assert list_params(count=500)["count"] == "500"
    assert list_params(count=20, offset=40, sort="-updated_at") == {
        "count": "20",
        "offset": "40",
        "sort": "-updated_at",
    }


async def test_list_page_query_and_result() -> None:
    rec = Recorder(httpx2.Response(200, json={"data": [{"id": 1}, {"id": 2}], "total": 7}))
    async with make_client(rec) as client:
        page = await client.list_page(
            "/pages", count=900, offset=2, sort="name", filters={"draft": True, "book_id": 3}
        )
    assert page == ListPage([{"id": 1}, {"id": 2}], 7)
    query = dict(rec.last.url.params)
    assert query == {
        "count": "500",
        "offset": "2",
        "sort": "name",
        "filter[draft]": "1",
        "filter[book_id]": "3",
    }
    assert b"true" not in rec.last.url.query


async def test_get_json_encodes_booleans() -> None:
    rec = Recorder()
    async with make_client(rec) as client:
        await client.get_json("/search", params={"query": "x", "flag": False, "skip": None})
    assert dict(rec.last.url.params) == {"query": "x", "flag": "0"}


async def test_post_and_put_json() -> None:
    rec = Recorder()
    async with make_client(rec) as client:
        await client.post_json("/pages", {"name": "A", "book_id": 1})
        assert rec.last.method == "POST"
        assert json.loads(rec.last.content) == {"name": "A", "book_id": 1}
        await client.put_json("/pages/1", {"name": "B"})
        assert rec.last.method == "PUT"
        assert json.loads(rec.last.content) == {"name": "B"}


async def test_delete_with_empty_body() -> None:
    rec = Recorder(httpx2.Response(204))
    async with make_client(rec) as client:
        await client.delete("/pages/1")
    assert rec.last.method == "DELETE"


async def test_multipart_method_override() -> None:
    rec = Recorder()
    async with make_client(rec) as client:
        await client.post_multipart(
            "/attachments/5",
            {"name": "f", "uploaded_to": 9},
            {"file": ("a.txt", b"hello", "text/plain")},
            method_override="PUT",
        )
    req = rec.last
    assert req.method == "POST"
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="_method"\r\n\r\nPUT' in req.content
    assert b'name="uploaded_to"\r\n\r\n9' in req.content
    assert b'filename="a.txt"' in req.content


async def test_multipart_without_override() -> None:
    rec = Recorder()
    async with make_client(rec) as client:
        await client.post_multipart("/attachments", {"name": "f"}, {"file": ("a", b"x")})
    assert b"_method" not in rec.last.content


async def test_get_text_decodes_charset() -> None:
    body = "Grüße".encode("latin-1")
    rec = Recorder(
        httpx2.Response(200, content=body, headers={"content-type": "text/plain; charset=latin-1"})
    )
    async with make_client(rec) as client:
        assert await client.get_text("/pages/1/export/plaintext") == "Grüße"


async def test_get_bytes_within_limit() -> None:
    rec = Recorder(httpx2.Response(200, content=b"%PDF-1234"))
    async with make_client(rec) as client:
        assert await client.get_bytes("/pages/1/export/pdf", max_bytes=9) == b"%PDF-1234"


async def test_get_bytes_stops_past_limit_while_streaming() -> None:
    sent: list[int] = []

    async def chunks() -> Any:
        for _ in range(100):
            sent.append(1)
            yield b"x" * 1000

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=chunks())

    async with make_client(handler) as client:
        with pytest.raises(BookStackResponseTooLarge) as info:
            await client.get_bytes("/pages/1/export/pdf", max_bytes=2500)
    assert info.value.max_bytes == 2500
    assert len(sent) < 100


async def test_get_bytes_rejects_declared_length() -> None:
    rec = Recorder(httpx2.Response(200, content=b"x" * 50))
    async with make_client(rec) as client:
        with pytest.raises(BookStackResponseTooLarge):
            await client.get_bytes("/image-gallery/1/data", max_bytes=10)


async def test_one_log_line_per_request(caplog: pytest.LogCaptureFixture) -> None:
    rec = Recorder()
    caplog.set_level(logging.INFO, logger="bookstack_mcp.client.http")
    async with make_client(rec) as client:
        await client.get_json("/pages", params={"secret_query": "q"})
    [record] = caplog.records
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/api/pages"  # type: ignore[attr-defined]
    assert record.status == 200  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, int)  # type: ignore[attr-defined]
    assert "secret_query" not in caplog.text
    assert "tsecret" not in caplog.text
