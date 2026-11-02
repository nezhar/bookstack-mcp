"""The async BookStack API client.

Every request goes through ``BookStackClient._request``: it waits for a rate-limit token, sends
the request, logs it, maps errors, and retries what the retry policy allows. Retries and rate
limiting belong there and nowhere else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from os import PathLike
from types import TracebackType
from typing import IO, Any, Protocol, Self

import httpx2
from pydantic import BaseModel, ConfigDict

from bookstack_mcp import __version__
from bookstack_mcp.client.errors import (
    BookStackError,
    BookStackResponseTooLarge,
    BookStackUnavailable,
    error_for_status,
)
from bookstack_mcp.client.links import Links
from bookstack_mcp.client.ratelimit import (
    DEFAULT_BURST,
    DEFAULT_REQUESTS_PER_MINUTE,
    Clock,
    Sleep,
    TokenBucket,
)
from bookstack_mcp.client.retry import RetryPolicy
from bookstack_mcp.client.version import Features, Version, parse_version

logger = logging.getLogger(__name__)

MAX_COUNT = 500
"""BookStack's largest ``count`` on list endpoints."""

MAX_ERROR_BODY_BYTES = 64 * 1024
"""How much of an error response is read to find BookStack's message."""

DEFAULT_TIMEOUT = 30.0

ParamValue = str | int | float | bool | None
FileContent = bytes | IO[bytes]
FileSpec = FileContent | tuple[str, FileContent] | tuple[str, FileContent, str]


class SecretLike(Protocol):
    """Anything with ``get_secret_value()``, such as pydantic's ``SecretStr``."""

    def get_secret_value(self) -> str: ...


@dataclass(frozen=True)
class RawResponse:
    """A response whose body has been read in full (within any size limit)."""

    status_code: int
    headers: httpx2.Headers
    content: bytes

    @property
    def charset(self) -> str:
        content_type = self.headers.get("content-type", "")
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                return value.strip("\"'")
        return "utf-8"


@dataclass(frozen=True)
class ListPage:
    """One page of a list endpoint: the ``data`` items and the ``total`` across all pages."""

    data: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0


class SystemInfo(BaseModel):
    """``GET /api/system``."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    version: str
    instance_id: str
    app_name: str
    app_logo: str | None = None
    base_url: str

    @property
    def version_tuple(self) -> Version:
        return parse_version(self.version)

    @property
    def features(self) -> Features:
        return Features.from_version(self.version)

    @property
    def links(self) -> Links:
        return Links(self.base_url)


def encode_value(value: ParamValue) -> str:
    """Query and form encoding: booleans become ``1``/``0`` (BookStack reads ``true`` wrongly)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def encode_params(params: Mapping[str, ParamValue] | None) -> dict[str, str]:
    """Encode query parameters, dropping ``None`` values."""
    if not params:
        return {}
    return {k: encode_value(v) for k, v in params.items() if v is not None}


def list_params(
    *,
    count: int | None = None,
    offset: int | None = None,
    sort: str | None = None,
    filters: Mapping[str, ParamValue] | None = None,
) -> dict[str, str]:
    """Query parameters for a list endpoint. ``count`` is clamped to 1..500."""
    params: dict[str, str] = {}
    if count is not None:
        params["count"] = str(max(1, min(count, MAX_COUNT)))
    if offset is not None:
        params["offset"] = str(max(0, offset))
    if sort:
        params["sort"] = sort
    for name, value in (filters or {}).items():
        if value is not None:
            params[f"filter[{name}]"] = encode_value(value)
    return params


def _ssl_verify(verify: bool | str | PathLike[str] | ssl.SSLContext) -> bool | ssl.SSLContext:
    if isinstance(verify, bool | ssl.SSLContext):
        return verify
    return ssl.create_default_context(cafile=str(verify))


def _api_path(path: str) -> str:
    return "/api/" + path.lstrip("/")


class BookStackClient:
    """Async client for the BookStack REST API.

    ``base_url`` is the instance URL without ``/api``; ``path`` arguments are relative to
    ``/api`` (``"/pages/12"``). Use as ``async with BookStackClient(...) as client`` or call
    ``aclose()``.

    ``requests_per_minute`` sets the client-side rate limit (burst 10); ``retry`` the retry
    policy. ``clock`` and ``sleep`` drive both and exist so tests need not wait for real.
    """

    def __init__(
        self,
        base_url: str,
        token_id: str,
        token_secret: str | SecretLike,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        verify: bool | str | PathLike[str] | ssl.SSLContext = True,
        user_agent: str | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        retry: RetryPolicy | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        secret = token_secret if isinstance(token_secret, str) else token_secret.get_secret_value()
        self.base_url = base_url.rstrip("/")
        self._http = httpx2.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Token {token_id}:{secret}",
                "Accept": "application/json",
                "User-Agent": user_agent or f"bookstack-mcp/{__version__}",
            },
            timeout=timeout,
            verify=_ssl_verify(verify),
            follow_redirects=False,
            transport=transport,
        )
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._limiter = TokenBucket(
            requests_per_minute, burst=DEFAULT_BURST, clock=clock, sleep=sleep
        )

    def __repr__(self) -> str:
        return f"BookStackClient(base_url={self.base_url!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # The single request path.

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, FileSpec] | None = None,
        max_bytes: int | None = None,
    ) -> RawResponse:
        """Send a request and return the response, or raise a ``BookStackError``.

        Each attempt first takes a token from the rate limiter. A failed attempt is retried as
        ``RetryPolicy`` allows: 429 for any method, 5xx, connection errors and timeouts only for
        GET, HEAD and OPTIONS. An upload is retried only if every file stream can be rewound.
        Logs one line per attempt and one per retry. With ``max_bytes`` the body is streamed and
        the read stops with ``BookStackResponseTooLarge`` past the limit.
        """
        api_path = _api_path(path)
        file_positions = _file_positions(files)
        waited_s = 0.0
        attempt = 1
        while True:
            await self._limiter.acquire()
            try:
                return await self._send(
                    method,
                    api_path,
                    attempt,
                    params=params,
                    json=json,
                    data=data,
                    files=files,
                    max_bytes=max_bytes,
                )
            except BookStackError as error:
                delay = self._retry.delay(method, error, attempt, waited_s)
                if delay is None or not _rewind_files(files, file_positions):
                    raise
                extra: dict[str, Any] = {
                    "method": method,
                    "path": api_path,
                    "attempt": attempt,
                    "retry_in_ms": round(delay * 1000),
                }
                if error.status is not None:
                    extra["status"] = error.status
                logger.info("bookstack retry", extra=extra)
                await self._sleep(delay)
                waited_s += delay
                attempt += 1

    async def _send(
        self,
        method: str,
        api_path: str,
        attempt: int,
        *,
        params: Mapping[str, str] | None,
        json: Any,
        data: Mapping[str, str] | None,
        files: Mapping[str, FileSpec] | None,
        max_bytes: int | None,
    ) -> RawResponse:
        """One attempt: send, read, map errors, and log one line."""
        started = time.monotonic()
        status: int | None = None
        try:
            async with self._http.stream(
                method, api_path, params=params, json=json, data=data, files=files
            ) as response:
                status = response.status_code
                if status >= 300:
                    body = await _read_limited(response, MAX_ERROR_BODY_BYTES, truncate=True)
                    raise error_for_status(status, response.headers, body)
                body = await _read_limited(response, max_bytes)
                return RawResponse(status, response.headers, body)
        except httpx2.TimeoutException as exc:
            raise BookStackUnavailable(
                f"BookStack did not answer {method} {api_path} in time ({type(exc).__name__})."
            ) from None
        except httpx2.TransportError as exc:
            raise BookStackUnavailable(
                f"Could not reach BookStack for {method} {api_path} ({type(exc).__name__})."
            ) from None
        finally:
            duration_ms = round((time.monotonic() - started) * 1000)
            extra: dict[str, Any] = {
                "method": method,
                "path": api_path,
                "duration_ms": duration_ms,
                "attempt": attempt,
            }
            if status is not None:
                extra["status"] = status
            logger.info("bookstack request", extra=extra)

    # JSON.

    async def get_json(self, path: str, params: Mapping[str, ParamValue] | None = None) -> Any:
        return _json(await self._request("GET", path, params=encode_params(params)))

    async def post_json(self, path: str, body: Any) -> Any:
        return _json(await self._request("POST", path, json=body))

    async def put_json(self, path: str, body: Any) -> Any:
        return _json(await self._request("PUT", path, json=body))

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def post_multipart(
        self,
        path: str,
        data: Mapping[str, ParamValue],
        files: Mapping[str, FileSpec],
        method_override: str | None = None,
    ) -> Any:
        """Multipart ``POST``. ``method_override="PUT"`` adds ``_method=PUT`` (PHP ignores form
        bodies on real PUT requests)."""
        form = encode_params(data)
        if method_override is not None:
            form["_method"] = method_override.upper()
        return _json(await self._request("POST", path, data=form, files=files))

    # Exports and binary data.

    async def get_text(self, path: str) -> str:
        """A text response, such as a markdown, plain-text or HTML export."""
        raw = await self._request("GET", path)
        return raw.content.decode(raw.charset, errors="replace")

    async def get_bytes(self, path: str, max_bytes: int) -> bytes:
        """A binary response, such as a PDF export, read up to ``max_bytes``."""
        if max_bytes < 0:
            raise ValueError("max_bytes must not be negative")
        return (await self._request("GET", path, max_bytes=max_bytes)).content

    # Lists.

    async def list_page(
        self,
        path: str,
        *,
        count: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
        filters: Mapping[str, ParamValue] | None = None,
    ) -> ListPage:
        """One page of a list endpoint. ``filters={"draft": False}`` sends ``filter[draft]=0``."""
        params = list_params(count=count, offset=offset, sort=sort, filters=filters)
        body = _json(await self._request("GET", path, params=params))
        if not isinstance(body, Mapping) or not isinstance(body.get("data"), list):
            raise BookStackError(
                f"GET {_api_path(path)} did not return a list.",
                hint="Use list_page only with BookStack list endpoints.",
            )
        items = [item for item in body["data"] if isinstance(item, dict)]
        total = body.get("total")
        return ListPage(items, total if isinstance(total, int) else len(items))

    # System.

    async def system(self) -> SystemInfo:
        """``GET /api/system``: version, instance id, app name and logo, base URL."""
        body = await self.get_json("/system")
        try:
            return SystemInfo.model_validate(body)
        except ValueError:
            raise BookStackError(
                "GET /api/system returned an unexpected body.",
                hint="Check that BOOKSTACK_URL points at a BookStack instance.",
            ) from None


def _file_stream(spec: FileSpec) -> IO[bytes] | None:
    content = spec[1] if isinstance(spec, tuple) else spec
    return None if isinstance(content, bytes) else content


def _file_positions(files: Mapping[str, FileSpec] | None) -> dict[str, int | None]:
    """Where each file stream starts, so a retry can rewind it; ``None`` if it cannot seek."""
    positions: dict[str, int | None] = {}
    for name, spec in (files or {}).items():
        stream = _file_stream(spec)
        if stream is None:
            continue
        try:
            positions[name] = stream.tell() if stream.seekable() else None
        except (OSError, ValueError):
            positions[name] = None
    return positions


def _rewind_files(
    files: Mapping[str, FileSpec] | None, positions: Mapping[str, int | None]
) -> bool:
    """Rewind every file stream for a resend. ``False`` if one cannot be rewound: a stream that
    has been read cannot be sent again, so the request must not be retried."""
    for name, position in positions.items():
        if position is None:
            return False
        stream = _file_stream((files or {})[name])
        if stream is None:
            continue
        try:
            stream.seek(position)
        except (OSError, ValueError):
            return False
    return True


async def _read_limited(
    response: httpx2.Response, max_bytes: int | None, *, truncate: bool = False
) -> bytes:
    """Read the body, stopping past ``max_bytes``: truncate, or raise if not ``truncate``."""
    if max_bytes is None:
        return await response.aread()
    if not truncate:
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise BookStackResponseTooLarge(max_bytes)
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            if truncate:
                chunks.append(chunk[: max_bytes - (size - len(chunk))])
                break
            raise BookStackResponseTooLarge(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _json(raw: RawResponse) -> Any:
    if not raw.content.strip():
        return None
    try:
        return json.loads(raw.content)
    except ValueError:
        raise BookStackError(
            "BookStack answered with a body that is not JSON.",
            status=raw.status_code,
            hint="Check that BOOKSTACK_URL is the BookStack instance URL without /api.",
        ) from None
