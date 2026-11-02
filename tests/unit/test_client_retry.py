"""Retries and rate limiting of BookStackClient, with an injected clock and sleep.

The responses are canned through httpx2.MockTransport: these tests check the client's policy,
never how BookStack answers.
"""

import io
import logging
import random
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from bookstack_mcp.client import (
    BookStackClient,
    BookStackRateLimited,
    BookStackServerError,
    BookStackUnavailable,
    BookStackValidationError,
)
from bookstack_mcp.client.ratelimit import TokenBucket
from bookstack_mcp.client.retry import RetryPolicy

Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeTime:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Script:
    """Answers with the given responses in order (the last one repeats) and records requests."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return response


def make_client(
    handler: Handler, fake: FakeTime, *, jitter: float = 0.0, **kwargs: Any
) -> BookStackClient:
    return BookStackClient(
        "https://docs.example.com",
        "tid",
        "tsecret",
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(random=lambda: jitter),
        clock=fake.clock,
        sleep=fake.sleep,
        **kwargs,
    )


OK = httpx2.Response(200, json={"ok": True})


def status(code: int, **headers: str) -> httpx2.Response:
    return httpx2.Response(code, json={"error": {"message": "nope"}}, headers=headers)


# Retries.


async def test_429_then_200_waits_retry_after() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"Retry-After": "3"}), OK)
    async with make_client(script, fake) as client:
        assert await client.get_json("/pages/1") == {"ok": True}
    assert len(script.requests) == 2
    assert fake.sleeps == [pytest.approx(3.25)]  # Retry-After plus 250 ms margin


async def test_429_is_retried_for_post() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"Retry-After": "1"}), OK)
    async with make_client(script, fake) as client:
        assert await client.post_json("/pages", {"name": "x"}) == {"ok": True}
    assert len(script.requests) == 2
    assert script.requests[0].content == script.requests[1].content


async def test_429_uses_ratelimit_reset_when_no_retry_after() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"X-RateLimit-Reset": "0"}), OK)  # already past
    async with make_client(script, fake) as client:
        await client.get_json("/pages/1")
    assert fake.sleeps == [pytest.approx(0.25)]


async def test_429_without_hint_uses_backoff() -> None:
    fake = FakeTime()
    script = Script(status(429), OK)
    async with make_client(script, fake) as client:
        await client.get_json("/pages/1")
    assert fake.sleeps == [0.5]


async def test_post_503_is_not_retried() -> None:
    fake = FakeTime()
    script = Script(status(503), OK)
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackServerError):
            await client.post_json("/pages", {"name": "x"})
    assert len(script.requests) == 1
    assert fake.sleeps == []


async def test_put_timeout_is_not_retried() -> None:
    fake = FakeTime()
    script = Script(httpx2.ReadTimeout("slow"), OK)
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackUnavailable):
            await client.put_json("/pages/1", {"name": "x"})
    assert len(script.requests) == 1


async def test_get_503_is_tried_4_times_with_doubling_backoff() -> None:
    fake = FakeTime()
    script = Script(status(503))
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackServerError):
            await client.get_json("/pages/1")
    assert len(script.requests) == 4
    assert fake.sleeps == [0.5, 1.0, 2.0]


@pytest.mark.parametrize("code", [500, 502, 503, 504])
async def test_get_5xx_retried(code: int) -> None:
    fake = FakeTime()
    script = Script(status(code), OK)
    async with make_client(script, fake) as client:
        assert await client.get_json("/pages/1") == {"ok": True}
    assert len(script.requests) == 2


async def test_get_501_is_not_retried() -> None:
    fake = FakeTime()
    script = Script(status(501), OK)
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackServerError):
            await client.get_json("/pages/1")
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    "exc", [httpx2.ConnectError("refused"), httpx2.ReadTimeout("slow")], ids=type
)
async def test_get_connection_errors_and_timeouts_retried(exc: Exception) -> None:
    fake = FakeTime()
    script = Script(exc, OK)
    async with make_client(script, fake) as client:
        assert await client.get_text("/pages/1/export/markdown") == '{"ok":true}'
    assert len(script.requests) == 2


async def test_client_errors_are_not_retried() -> None:
    fake = FakeTime()
    script = Script(status(422), OK)
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackValidationError):
            await client.get_json("/pages/1")
    assert len(script.requests) == 1


async def test_503_retry_after_is_honoured() -> None:
    fake = FakeTime()
    script = Script(status(503, **{"Retry-After": "2"}), OK)
    async with make_client(script, fake) as client:
        await client.get_json("/pages/1")
    assert fake.sleeps == [pytest.approx(2.25)]


async def test_budget_caps_server_directed_wait() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"Retry-After": "100"}))
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackRateLimited):
            await client.get_json("/pages/1")
    # The first wait is capped at the 30 s budget, after which nothing is left.
    assert fake.sleeps == [30.0]
    assert len(script.requests) == 2


async def test_budget_stops_early_when_backoff_would_pass_it() -> None:
    fake = FakeTime()
    # 20 s server wait, then backoff of 1 s fits (21 s), then a 9.5 s server wait is capped to
    # the 9 s left; the 4th attempt fails and the error surfaces.
    script = Script(
        status(429, **{"Retry-After": "19.75"}),
        status(503),
        status(429, **{"Retry-After": "9.25"}),
        OK,
    )
    async with make_client(script, fake) as client:
        assert await client.get_json("/pages/1") == {"ok": True}
    assert fake.sleeps == [pytest.approx(20.0), 1.0, pytest.approx(9.0)]

    policy = RetryPolicy(random=lambda: 0.0)
    error = BookStackServerError("x", status=503)
    assert policy.delay("GET", error, attempt=1, waited_s=29.0) == 0.5
    # A backoff wait that would pass the budget is not taken.
    assert policy.delay("GET", error, attempt=2, waited_s=29.5) is None
    assert policy.delay("GET", error, attempt=1, waited_s=30.0) is None


async def test_budget_surfaces_error_at_once() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"Retry-After": "29.5"}), status(503))
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackServerError):
            await client.get_json("/pages/1")
    # 29.75 s waited; a 1 s backoff would pass 30 s, so the 503 surfaces without a third try.
    assert fake.sleeps == [pytest.approx(29.75)]
    assert len(script.requests) == 2


def test_backoff_steps_and_cap() -> None:
    policy = RetryPolicy(random=lambda: 0.0)
    assert [policy.backoff(n) for n in range(1, 8)] == [0.5, 1, 2, 4, 8, 8, 8]


@pytest.mark.parametrize("attempt", [1, 2, 3, 4, 5, 6])
def test_jitter_stays_within_25_percent(attempt: int) -> None:
    rng = random.Random(attempt)  # noqa: S311 - jitter, not crypto
    policy = RetryPolicy(random=rng.random)
    base = RetryPolicy(random=lambda: 0.0).backoff(attempt)
    for _ in range(500):
        assert base <= policy.backoff(attempt) <= base * 1.25
    assert RetryPolicy(random=lambda: 1.0).backoff(attempt) == base * 1.25


async def test_retry_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bookstack_mcp.client.http")
    fake = FakeTime()
    script = Script(status(503), OK)
    async with make_client(script, fake) as client:
        await client.get_json("/pages/1", {"q": "secret_query"})
    retries = [r for r in caplog.records if r.getMessage() == "bookstack retry"]
    [retry] = retries
    assert retry.levelno == logging.INFO
    assert retry.__dict__["method"] == "GET"
    assert retry.__dict__["path"] == "/api/pages/1"
    assert retry.__dict__["status"] == 503
    assert retry.__dict__["attempt"] == 1
    assert retry.__dict__["retry_in_ms"] == 500
    requests = [r for r in caplog.records if r.getMessage() == "bookstack request"]
    assert [r.__dict__["attempt"] for r in requests] == [1, 2]
    assert "secret_query" not in caplog.text


async def test_connection_retry_logged_without_status(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bookstack_mcp.client.http")
    fake = FakeTime()
    async with make_client(Script(httpx2.ConnectError("x"), OK), fake) as client:
        await client.get_json("/pages/1")
    [retry] = [r for r in caplog.records if r.getMessage() == "bookstack retry"]
    assert "status" not in retry.__dict__


# Uploads.


async def test_upload_429_rewinds_file_stream() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"Retry-After": "1"}), OK)
    stream = io.BytesIO(b"image-bytes")
    async with make_client(script, fake) as client:
        await client.post_multipart(
            "/image-gallery", {"type": "gallery"}, {"image": ("a.png", stream)}
        )
    assert len(script.requests) == 2
    assert b"image-bytes" in script.requests[1].content


class OneShotStream(io.RawIOBase):
    def __init__(self, data: bytes) -> None:
        self._data = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readinto(self, buffer: Any) -> int:
        chunk = self._data.read(len(buffer))
        buffer[: len(chunk)] = chunk
        return len(chunk)


async def test_upload_with_unseekable_stream_is_not_retried() -> None:
    fake = FakeTime()
    script = Script(status(429, **{"Retry-After": "1"}), OK)
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackRateLimited):
            await client.post_multipart(
                "/attachments", {"name": "a"}, {"file": ("a.bin", OneShotStream(b"data"))}
            )
    assert len(script.requests) == 1


async def test_upload_from_bytes_is_retried_on_429() -> None:
    fake = FakeTime()
    script = Script(status(429), OK)
    async with make_client(script, fake) as client:
        await client.post_multipart("/attachments", {"name": "a"}, {"file": ("a.bin", b"data")})
    assert len(script.requests) == 2
    assert b"data" in script.requests[1].content


async def test_upload_503_is_not_retried() -> None:
    fake = FakeTime()
    script = Script(status(503), OK)
    async with make_client(script, fake) as client:
        with pytest.raises(BookStackServerError):
            await client.post_multipart("/attachments", {"name": "a"}, {"file": b"data"})
    assert len(script.requests) == 1


# Rate limiter.


async def test_bucket_allows_burst_of_10_then_150_per_minute() -> None:
    fake = FakeTime()
    bucket = TokenBucket(150, clock=fake.clock, sleep=fake.sleep)
    start = fake.now
    for _ in range(10):
        assert await bucket.acquire() == 0
    assert fake.sleeps == []
    for _ in range(150):
        await bucket.acquire()
    # 150 more after the burst take one minute: one token every 0.4 s.
    assert fake.now - start == pytest.approx(60.0)
    assert all(s == pytest.approx(0.4) for s in fake.sleeps)


async def test_bucket_refills_continuously_up_to_burst() -> None:
    fake = FakeTime()
    bucket = TokenBucket(60, clock=fake.clock, sleep=fake.sleep)
    for _ in range(10):
        await bucket.acquire()
    fake.now += 3.5  # 3.5 tokens back
    for _ in range(3):
        assert await bucket.acquire() == 0
    assert await bucket.acquire() == pytest.approx(0.5)
    fake.now += 3600  # long idle: capped at the burst of 10
    for _ in range(10):
        assert await bucket.acquire() == 0
    assert await bucket.acquire() == pytest.approx(1.0)


async def test_bucket_does_not_spin_on_a_clock_that_does_not_move() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    bucket = TokenBucket(150, clock=lambda: 0.0, sleep=sleep)
    for _ in range(12):
        await bucket.acquire()
    assert sleeps == [pytest.approx(0.4), pytest.approx(0.4)]


def test_bucket_rejects_bad_rate() -> None:
    with pytest.raises(ValueError, match="requests_per_minute"):
        TokenBucket(0)


async def test_client_requests_go_through_the_limiter() -> None:
    fake = FakeTime()
    script = Script(OK)
    async with make_client(script, fake, requests_per_minute=60) as client:
        for _ in range(12):
            await client.get_json("/pages/1")
    assert len(script.requests) == 12
    assert fake.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]


async def test_retries_take_tokens_too() -> None:
    fake = FakeTime()
    script = Script(status(503), OK)
    async with make_client(script, fake, requests_per_minute=60) as client:
        for _ in range(9):
            await client.get_json("/pages/1")  # 9 tokens
        await client.get_json("/pages/2")  # 503 (token 10), retry after 0.5 s
    # The retry found half a token after its 0.5 s backoff and waited 0.5 s more.
    assert fake.sleeps == [0.5, pytest.approx(0.5)]
