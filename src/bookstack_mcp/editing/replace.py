# Portions ported from pnocera/bookstack-mcp-server v2.1.1 (src/utils/page-content.ts,
# src/tools/pages.ts), used under the MIT License:
#
# Copyright (c) 2025 BookStack MCP Server Contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Literal find-and-replace over page source, applied as a chain of edits."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from bookstack_mcp.editing.errors import (
    AnchorAmbiguous,
    AnchorNotFound,
    InvalidArgument,
    InvalidEdit,
)

CONTEXT_RADIUS = 120
NEAR_MISS_MAX = 300
AMBIGUOUS_SHOWN = 3
ELLIPSIS = "…"
_SPACE = re.compile(r"\s+")


class EditLike(Protocol):
    """Anything with the three edit fields, such as ``Edit`` or a pydantic tool input."""

    @property
    def old_string(self) -> str: ...

    @property
    def new_string(self) -> str: ...

    @property
    def replace_all(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Edit:
    """Replace ``old_string`` with ``new_string``; every occurrence if ``replace_all``."""

    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass(frozen=True, slots=True)
class AppliedEdit:
    """What one edit did. ``context`` surrounds the (first) change in the new text."""

    index: int
    occurrences_replaced: int
    context: str


@dataclass(frozen=True, slots=True)
class EditResult:
    """The text after every edit, and one ``AppliedEdit`` per edit in order."""

    text: str
    applied: list[AppliedEdit]


def apply_edits(text: str, edits: Sequence[EditLike]) -> EditResult:
    """Apply literal edits in order, each to the result of the previous one.

    An ``old_string`` must occur exactly once unless ``replace_all`` is set, so an
    ambiguous anchor never patches the wrong place. Raises ``InvalidArgument`` for
    no edits, ``InvalidEdit`` for an empty or no-op edit, ``AnchorNotFound`` and
    ``AnchorAmbiguous``. Nothing is applied unless every edit succeeds.
    """
    if not edits:
        raise InvalidArgument("At least one edit is required.")
    current = text
    applied: list[AppliedEdit] = []
    for index, edit in enumerate(edits):
        old, new = edit.old_string, edit.new_string
        if not old:
            raise InvalidEdit(index, "old_string must not be empty.")
        if old == new:
            raise InvalidEdit(index, "old_string and new_string are identical.")
        count = current.count(old)
        if count == 0:
            raise AnchorNotFound(index, _near_miss(current, old))
        if count > 1 and not edit.replace_all:
            raise AnchorAmbiguous(index, count, _first_excerpts(current, old))
        first = current.find(old)
        if edit.replace_all:
            current = current.replace(old, new)
        else:
            current = current[:first] + new + current[first + len(old) :]
        applied.append(AppliedEdit(index, count, excerpt(current, first, first + len(new))))
    return EditResult(current, applied)


def excerpt(text: str, start: int, end: int, radius: int = CONTEXT_RADIUS) -> str:
    """Return ``text[start:end]`` with ``radius`` characters either side, ``…`` where cut."""
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return (ELLIPSIS if lo > 0 else "") + text[lo:hi] + (ELLIPSIS if hi < len(text) else "")


def _first_excerpts(text: str, needle: str) -> list[str]:
    found: list[str] = []
    at = text.find(needle)
    while at != -1 and len(found) < AMBIGUOUS_SHOWN:
        found.append(excerpt(text, at, at + len(needle)))
        at = text.find(needle, at + len(needle))
    return found


def _near_miss(text: str, needle: str) -> str | None:
    """Find ``needle`` again allowing any run of whitespace where it has whitespace.

    Stored HTML often differs from text copied out of a rendered view only in line
    breaks and indentation.
    """
    parts = _SPACE.split(needle.strip())
    if parts == [""]:
        return None
    match = re.search(r"\s+".join(re.escape(p) for p in parts), text)
    if match is None:
        return None
    found = match.group(0)
    return found[:NEAR_MISS_MAX] + ELLIPSIS if len(found) > NEAR_MISS_MAX else found
