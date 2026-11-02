"""Errors raised by the BookStack API client.

BookStack renders API errors as ``{"error": {"message": ..., "code": ..., "validation": {...}}}``.
``error`` can also be a plain string, and a proxy in front of BookStack may answer with HTML.
Every error carries a ``hint``: one sentence on how to fix it. Messages never include the token,
request bodies or response content other than BookStack's own error message.
"""

from __future__ import annotations

import email.utils
import json
import time
from collections.abc import Mapping
from typing import Any

MAX_MESSAGE_CHARS = 500


class BookStackError(Exception):
    """Base class for errors from the BookStack API client.

    Also raised directly for an HTTP status without a dedicated subclass (such as 400 or 405)
    and for a response the client cannot use (such as a redirect or a body that is not JSON).
    """

    default_hint = "Check the request and the BookStack server logs."

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.message = message
        self.status = status
        self.code = code
        self.hint = hint if hint is not None else self.default_hint
        super().__init__(message)

    def __str__(self) -> str:
        prefix = f"HTTP {self.status}: " if self.status is not None else ""
        return f"{prefix}{self.message} Hint: {self.hint}"


class BookStackAuthError(BookStackError):
    """401: the API token is wrong, expired or missing."""

    default_hint = (
        "Check BOOKSTACK_TOKEN_ID and BOOKSTACK_TOKEN_SECRET; the token may be wrong or expired."
    )


class BookStackForbidden(BookStackError):
    """403: the token's user lacks a permission. BookStack's message says which."""

    default_hint = "Give the API token's user a role with the permission this action needs."
    api_access_hint = 'Give the API token\'s user a role with the "Access system API" permission.'


class BookStackNotFound(BookStackError):
    """404: the item does not exist, or exists but this user may not see it."""

    default_hint = "Check the id; the item may be missing or not visible to the API token's user."


class BookStackValidationError(BookStackError):
    """422: BookStack rejected the input. ``field_errors`` maps field names to messages."""

    default_hint = "Fix the fields listed in field_errors and try again."

    def __init__(
        self,
        message: str,
        *,
        field_errors: Mapping[str, list[str]] | None = None,
        status: int | None = 422,
        code: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.field_errors: dict[str, list[str]] = dict(field_errors or {})
        super().__init__(message, status=status, code=code, hint=hint)

    def __str__(self) -> str:
        base = super().__str__()
        if not self.field_errors:
            return base
        fields = "; ".join(f"{k}: {' '.join(v)}" for k, v in self.field_errors.items())
        return f"{base} Fields: {fields}"


class BookStackPayloadTooLarge(BookStackError):
    """413: the upload is over PHP's post size limit on the BookStack server."""

    default_hint = (
        "Upload a smaller file, or raise post_max_size and upload_max_filesize in the "
        "BookStack server's PHP configuration."
    )


class BookStackRateLimited(BookStackError):
    """429: over the API rate limit. ``retry_after_s`` says when to try again, if known."""

    default_hint = "Wait before retrying, or raise API_REQUESTS_PER_MIN on the BookStack server."

    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float | None = None,
        status: int | None = 429,
        code: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(message, status=status, code=code, hint=hint)


class BookStackServerError(BookStackError):
    """5xx: BookStack or a proxy in front of it failed."""

    default_hint = "Try again later; if it persists, check the BookStack server logs."


class BookStackUnavailable(BookStackError):
    """The server could not be reached, or did not answer in time."""

    default_hint = (
        "Check BOOKSTACK_URL, the network and TLS settings, and that BookStack is running."
    )


class BookStackResponseTooLarge(BookStackError):
    """A download is larger than the caller's ``max_bytes`` limit."""

    default_hint = "The file is over the size limit for this tool; download it from BookStack."

    def __init__(self, max_bytes: int, *, hint: str | None = None) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"The response is larger than {max_bytes} bytes.", hint=hint)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > MAX_MESSAGE_CHARS:
        return text[: MAX_MESSAGE_CHARS - 1] + "…"
    return text


def _field_errors(validation: Any) -> dict[str, list[str]]:
    if not isinstance(validation, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for field, messages in validation.items():
        if isinstance(messages, str):
            result[str(field)] = [_clip(messages)]
        elif isinstance(messages, list):
            result[str(field)] = [_clip(str(m)) for m in messages]
        else:
            result[str(field)] = [_clip(str(messages))]
    return result


def parse_error_body(content: bytes) -> tuple[str | None, int | None, dict[str, list[str]]]:
    """Return BookStack's error ``(message, code, field_errors)`` from a response body.

    Anything that is not BookStack's error JSON gives ``(None, None, {})``, so that HTML from a
    proxy never ends up in a message.
    """
    try:
        body = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return None, None, {}
    if not isinstance(body, Mapping):
        return None, None, {}
    error = body.get("error")
    if isinstance(error, str):
        return (_clip(error) or None), None, {}
    if not isinstance(error, Mapping):
        return None, None, {}
    raw_message = error.get("message")
    message = _clip(raw_message) if isinstance(raw_message, str) and raw_message.strip() else None
    raw_code = error.get("code")
    code = raw_code if isinstance(raw_code, int) and not isinstance(raw_code, bool) else None
    return message, code, _field_errors(error.get("validation"))


def parse_retry_after(headers: Mapping[str, str], *, now: float | None = None) -> float | None:
    """Seconds to wait, from ``Retry-After`` (seconds or HTTP date) or ``X-RateLimit-Reset``."""
    now = time.time() if now is None else now
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        value = retry_after.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            return max(0.0, when.timestamp() - now)
    reset = headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            return max(0.0, float(reset.strip()) - now)
        except ValueError:
            pass
    return None


API_ACCESS_MESSAGE = "does not have permission to make API calls"


def error_for_status(status: int, headers: Mapping[str, str], content: bytes) -> BookStackError:
    """Map an error response to the matching ``BookStackError``."""
    message, code, field_errors = parse_error_body(content)
    if status == 401:
        return BookStackAuthError(
            message or "BookStack rejected the API token.", status=status, code=code
        )
    if status == 403:
        hint = None
        if message is not None and API_ACCESS_MESSAGE in message:
            hint = BookStackForbidden.api_access_hint
        return BookStackForbidden(
            message or "The API token's user is not allowed to do this.",
            status=status,
            code=code,
            hint=hint,
        )
    if status == 404:
        return BookStackNotFound(
            message or "Not found, or not visible to the API token's user.",
            status=status,
            code=code,
        )
    if status == 413:
        return BookStackPayloadTooLarge(
            message or "The upload is too large for the BookStack server.",
            status=status,
            code=code,
        )
    if status == 422:
        return BookStackValidationError(
            message or "BookStack rejected the input.",
            field_errors=field_errors,
            status=status,
            code=code,
        )
    if status == 429:
        return BookStackRateLimited(
            message or "Too many requests to the BookStack API.",
            retry_after_s=parse_retry_after(headers),
            status=status,
            code=code,
        )
    if status >= 500:
        return BookStackServerError(
            message or "BookStack answered with a server error.", status=status, code=code
        )
    if 300 <= status < 400:
        return BookStackError(
            "BookStack answered with a redirect instead of an API response.",
            status=status,
            code=code,
            hint="Set BOOKSTACK_URL to the instance URL without /api, using the scheme and "
            "host BookStack is configured with (APP_URL).",
        )
    return BookStackError(message or "BookStack rejected the request.", status=status, code=code)
