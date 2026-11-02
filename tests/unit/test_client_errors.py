"""Error mapping of BookStackClient, through httpx2.MockTransport."""

import email.utils
import logging
import time
from typing import Any

import httpx2
import pytest

from bookstack_mcp.client import (
    BookStackAuthError,
    BookStackClient,
    BookStackError,
    BookStackForbidden,
    BookStackNotFound,
    BookStackPayloadTooLarge,
    BookStackRateLimited,
    BookStackServerError,
    BookStackUnavailable,
    BookStackValidationError,
)
from bookstack_mcp.client.errors import parse_error_body, parse_retry_after
from bookstack_mcp.client.retry import RetryPolicy

SECRET = "tsecret-do-not-leak"  # noqa: S105 - fake token


def client_for(response: httpx2.Response | Exception) -> BookStackClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if isinstance(response, Exception):
            raise response
        return response

    # One attempt only: these tests are about error mapping; retries are in test_client_retry.
    return BookStackClient(
        "https://docs.example.com",
        "tid",
        SECRET,
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(max_attempts=1),
    )


async def raised(response: httpx2.Response | Exception, **kwargs: Any) -> BookStackError:
    async with client_for(response) as client:
        with pytest.raises(BookStackError) as info:
            await client.post_json("/pages", {"name": "body-do-not-leak"})
    exc = info.value
    assert exc.hint
    assert SECRET not in str(exc)
    assert "body-do-not-leak" not in str(exc)
    return exc


def bs_error(status: int, message: str, **extra: Any) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"message": message, "code": status, **extra}})


@pytest.mark.parametrize(
    ("status", "cls"),
    [
        (401, BookStackAuthError),
        (403, BookStackForbidden),
        (404, BookStackNotFound),
        (413, BookStackPayloadTooLarge),
        (422, BookStackValidationError),
        (429, BookStackRateLimited),
        (500, BookStackServerError),
        (502, BookStackServerError),
        (503, BookStackServerError),
    ],
)
async def test_status_mapping(status: int, cls: type[BookStackError]) -> None:
    exc = await raised(bs_error(status, "Something went wrong"))
    assert type(exc) is cls
    assert exc.status == status
    assert exc.code == status
    assert exc.message == "Something went wrong"


@pytest.mark.parametrize("status", [400, 405, 409])
async def test_other_client_errors_use_base_class(status: int) -> None:
    exc = await raised(bs_error(status, "Nope"))
    assert type(exc) is BookStackError
    assert exc.status == status


async def test_redirect_is_an_error() -> None:
    exc = await raised(httpx2.Response(302, headers={"location": "https://docs.example.com/login"}))
    assert type(exc) is BookStackError
    assert "BOOKSTACK_URL" in exc.hint


async def test_forbidden_keeps_message_and_hints_api_access() -> None:
    message = "The owner of the used API token does not have permission to make API calls"
    exc = await raised(bs_error(403, message))
    assert exc.message == message
    assert "Access system API" in exc.hint


async def test_validation_field_errors() -> None:
    exc = await raised(
        bs_error(
            422,
            "The given data was invalid.",
            validation={"name": ["The name field is required."], "book_id": "Bad book."},
        )
    )
    assert isinstance(exc, BookStackValidationError)
    assert exc.field_errors == {"name": ["The name field is required."], "book_id": ["Bad book."]}
    assert "The name field is required." in str(exc)


async def test_plain_string_error() -> None:
    exc = await raised(httpx2.Response(404, json={"error": "No page here"}))
    assert isinstance(exc, BookStackNotFound)
    assert exc.message == "No page here"
    assert exc.code is None


async def test_non_json_body_is_not_echoed() -> None:
    html = "<html><body>proxy error page with secrets-in-html</body></html>"
    exc = await raised(httpx2.Response(502, text=html))
    assert isinstance(exc, BookStackServerError)
    assert "secrets-in-html" not in str(exc)
    assert exc.message


async def test_empty_body() -> None:
    exc = await raised(httpx2.Response(401))
    assert isinstance(exc, BookStackAuthError)
    assert "BOOKSTACK_TOKEN" in exc.hint


async def test_validation_without_details() -> None:
    exc = await raised(httpx2.Response(422, json={"message": "x"}))
    assert isinstance(exc, BookStackValidationError)
    assert exc.field_errors == {}


async def test_rate_limited_retry_after_seconds() -> None:
    exc = await raised(
        httpx2.Response(429, json={"error": {"message": "Too many"}}, headers={"Retry-After": "17"})
    )
    assert isinstance(exc, BookStackRateLimited)
    assert exc.retry_after_s == 17.0


async def test_rate_limited_ratelimit_reset() -> None:
    reset = str(int(time.time()) + 30)
    exc = await raised(httpx2.Response(429, headers={"X-RateLimit-Reset": reset}))
    assert isinstance(exc, BookStackRateLimited)
    assert exc.retry_after_s is not None
    assert 25 <= exc.retry_after_s <= 31


async def test_rate_limited_without_headers() -> None:
    exc = await raised(httpx2.Response(429))
    assert isinstance(exc, BookStackRateLimited)
    assert exc.retry_after_s is None


def test_parse_retry_after_http_date() -> None:
    now = 1_800_000_000.0
    date = email.utils.formatdate(now + 12, usegmt=True)
    assert parse_retry_after({"retry-after": date}, now=now) == pytest.approx(12.0)
    assert parse_retry_after({"retry-after": "garbage"}, now=now) is None
    assert parse_retry_after({"x-ratelimit-reset": str(now - 5)}, now=now) == 0.0


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"error": {"message": "m", "code": 7}}', ("m", 7, {})),
        (b'{"error": "plain"}', ("plain", None, {})),
        (b'{"error": {"message": ""}}', (None, None, {})),
        (b'{"error": 5}', (None, None, {})),
        (b"[1, 2]", (None, None, {})),
        (b"not json", (None, None, {})),
        (b"\xff\xfe", (None, None, {})),
    ],
)
def test_parse_error_body_shapes(body: bytes, expected: Any) -> None:
    assert parse_error_body(body) == expected


def test_long_messages_are_clipped() -> None:
    message, _, _ = parse_error_body(('{"error": "' + "a" * 2000 + '"}').encode())
    assert message is not None
    assert len(message) == 500


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ConnectError("connection refused"),
        httpx2.ReadTimeout("timed out"),
        httpx2.ConnectTimeout("timed out"),
        httpx2.RemoteProtocolError("bad"),
    ],
)
async def test_transport_errors_become_unavailable(error: Exception) -> None:
    exc = await raised(error)
    assert isinstance(exc, BookStackUnavailable)
    assert exc.status is None
    assert exc.__cause__ is None


async def test_errors_are_logged_without_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bookstack_mcp.client.http")
    await raised(bs_error(404, "missing"))
    [record] = caplog.records
    assert record.status == 404  # type: ignore[attr-defined]
    assert "body-do-not-leak" not in caplog.text


async def test_unavailable_is_logged_without_status(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bookstack_mcp.client.http")
    await raised(httpx2.ConnectError("refused"))
    [record] = caplog.records
    assert not hasattr(record, "status")


async def test_non_json_success_body() -> None:
    exc = await raised(httpx2.Response(200, text="<html>login</html>"))
    assert type(exc) is BookStackError
    assert "login" not in str(exc)


async def test_list_page_rejects_non_list() -> None:
    async with client_for(httpx2.Response(200, json={"id": 1})) as client:
        with pytest.raises(BookStackError, match="did not return a list"):
            await client.list_page("/pages/1")


async def test_system_rejects_unexpected_body() -> None:
    async with client_for(httpx2.Response(200, json={"nope": 1})) as client:
        with pytest.raises(BookStackError, match="unexpected body"):
            await client.system()
