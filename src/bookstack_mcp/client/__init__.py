"""Async BookStack API client on httpx2: auth, errors, retries and rate limiting."""

from bookstack_mcp.client.errors import (
    BookStackAuthError,
    BookStackError,
    BookStackForbidden,
    BookStackNotFound,
    BookStackPayloadTooLarge,
    BookStackRateLimited,
    BookStackResponseTooLarge,
    BookStackServerError,
    BookStackUnavailable,
    BookStackValidationError,
)
from bookstack_mcp.client.http import BookStackClient, ListPage, SystemInfo
from bookstack_mcp.client.links import Links
from bookstack_mcp.client.version import Features, Version, parse_version

__all__ = [
    "BookStackAuthError",
    "BookStackClient",
    "BookStackError",
    "BookStackForbidden",
    "BookStackNotFound",
    "BookStackPayloadTooLarge",
    "BookStackRateLimited",
    "BookStackResponseTooLarge",
    "BookStackServerError",
    "BookStackUnavailable",
    "BookStackValidationError",
    "Features",
    "Links",
    "ListPage",
    "SystemInfo",
    "Version",
    "parse_version",
]
