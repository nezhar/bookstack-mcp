"""BookStack version parsing and the API features each version has."""

from __future__ import annotations

import re
from dataclasses import dataclass

Version = tuple[int, int, int]

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(value: str) -> Version:
    """``"v26.09.2"`` -> ``(26, 9, 2)``. A missing patch number counts as 0.

    Raises ``ValueError`` for a string that does not start with a version number.
    """
    match = _VERSION_RE.match(value)
    if match is None:
        raise ValueError(f"not a BookStack version: {value!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


@dataclass(frozen=True)
class Features:
    """API features available on a BookStack version."""

    changelog: bool
    """Page create/update accept ``changelog`` (26.09+)."""
    scoped_search: bool
    """``/api/search/book/{id}`` and ``/api/search/chapter/{id}`` (26.09+)."""
    tags_api: bool
    """``/api/tags`` (26.05+)."""
    comments_api: bool
    """``/api/comments`` (25.11+)."""
    image_data: bool
    """Image gallery data endpoints (25.11+)."""
    imports_api: bool
    """``/api/imports`` (25.07+)."""

    @classmethod
    def from_version(cls, version: str | Version) -> Features:
        v = parse_version(version) if isinstance(version, str) else version
        return cls(
            changelog=v >= (26, 9, 0),
            scoped_search=v >= (26, 9, 0),
            tags_api=v >= (26, 5, 0),
            comments_api=v >= (25, 11, 0),
            image_data=v >= (25, 11, 0),
            imports_api=v >= (25, 7, 0),
        )
