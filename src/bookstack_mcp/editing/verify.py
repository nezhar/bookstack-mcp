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

"""Check, after a write, that BookStack stored what was sent.

BookStack rewrites stored HTML (ids on headings and paragraphs, formatting), so the
comparison is on normalised text, not bytes. Fragments carrying markup are compared on
the markup itself, with BookStack's rewrites undone on both sides.
"""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass

from bookstack_mcp.editing._text import plain_text
from bookstack_mcp.editing.append import prepare_content
from bookstack_mcp.editing.replace import EditLike
from bookstack_mcp.editing.source import SourceField

_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[A-Za-z/!][^>]*>")
# What BookStack changes when it saves HTML: ids on top-level elements, `/>` endings and
# single-quoted attributes.
_BOOKSTACK_ID = re.compile(r'\s+id="bkmrk-[^"]*"')
_SELF_CLOSING = re.compile(r"\s*/>")
_SINGLE_QUOTED = re.compile(r"=\s*'([^']*)'")


@dataclass(frozen=True, slots=True)
class Removed:
    """A fragment that may occur at most ``remaining`` times in the stored text.

    Occurrences are counted on normalised text, or with ``raw`` on the raw text with
    whitespace collapsed (for markup-only changes, whose normalised text is unchanged).
    """

    fragment: str
    remaining: int = 0
    raw: bool = False


@dataclass(frozen=True, slots=True)
class Checks:
    """Fragments that must appear in the stored text, and removed fragments that must not.

    ``must_equal`` is for a full replacement: the stored text must be that text, allowing
    for BookStack's rewrites (whitespace, and for HTML ids, ``/>`` endings and quoting).
    """

    must_contain: list[str]
    must_not_contain: list[Removed]
    must_equal: str | None = None


@dataclass(frozen=True, slots=True)
class Verification:
    """``verified`` is true when every check passed; ``unverified_count`` counts failures."""

    verified: bool
    unverified_count: int


def normalise(text: str, field: SourceField) -> str:
    """For HTML strip tags and decode common entities; then collapse whitespace and trim."""
    if field == "html":
        return plain_text(text)
    return _collapse(text)


def contains(haystack: str, fragment: str, field: SourceField) -> bool:
    """Whether ``fragment`` occurs in ``haystack`` once both are normalised.

    On HTML, a fragment holding a tag (``<b>a</b>``, ``<hr>``) must occur in the markup,
    so added formatting is checked and not just its text. A fragment that is not visible
    text, such as an attribute (``href="new"``), may match the markup too.
    """
    needle = normalise(fragment, field)
    if field != "html":
        return (needle or _collapse(fragment)) in _collapse(haystack)
    in_markup = _markup(fragment) in _markup(haystack)
    if not needle or _TAG.search(fragment):
        return in_markup
    return needle in normalise(haystack, field) or in_markup


def checks_for_edits(
    before: str, after: str, edits: Sequence[EditLike], field: SourceField
) -> Checks:
    """Build the checks for a chain of edits that turned ``before`` into ``after``.

    Each non-empty ``new_string`` still present in ``after`` must be stored. An
    ``old_string`` is checked when the new text cannot prove the edit landed (its
    ``new_string`` is empty, was overwritten by a later edit, or already existed in
    ``before``) and ``after`` holds fewer of it than ``before``, or none: the stored
    text may then hold no more of it than ``after``. Counting, not presence, keeps
    removed text that also occurs elsewhere checkable.

    Occurrences are counted on normalised text, except when the old text's
    normalised form is empty or contained in the new one's (a markup-only change
    such as a link's ``href`` or ``<b>`` to ``<em>``): those are counted on the raw
    text, which still shows whether the write stuck.
    """
    must_contain = [e.new_string for e in edits if e.new_string and e.new_string in after]
    must_not_contain: list[Removed] = []
    for e in edits:
        if e.new_string and e.new_string in after and not contains(before, e.new_string, field):
            continue
        old_text = normalise(e.old_string, field)
        removed = Removed(
            e.old_string, raw=not old_text or old_text in normalise(e.new_string, field)
        )
        left = _count(after, removed, field)
        if left == 0 or left < _count(before, removed, field):
            must_not_contain.append(Removed(removed.fragment, left, removed.raw))
    return Checks(must_contain, must_not_contain)


def checks_for_insert(content: str, field: SourceField) -> Checks:
    """Build the checks for an insert: the content, as ``insert`` wrote it, must be stored."""
    return Checks([prepare_content(content, field)], [])


def verify(stored: str, field: SourceField, checks: Checks) -> Verification:
    """Run ``checks`` against the source read back after the write. Never raises."""
    missing = sum(1 for f in checks.must_contain if not contains(stored, f, field))
    present = sum(1 for r in checks.must_not_contain if _count(stored, r, field) > r.remaining)
    differs = checks.must_equal is not None and not _equal(stored, checks.must_equal, field)
    failures = missing + present + int(differs)
    return Verification(failures == 0, failures)


def _count(text: str, removed: Removed, field: SourceField) -> int:
    """Count ``removed.fragment`` in ``text`` on normalised or whitespace-collapsed raw text."""
    if not removed.raw:
        return normalise(text, field).count(normalise(removed.fragment, field))
    needle = _collapse(removed.fragment)
    if not needle:  # whitespace only: count it exactly
        return text.count(removed.fragment)
    if field == "html":
        return _markup(text).count(_markup(removed.fragment))
    return _collapse(text).count(needle)


def _equal(stored: str, sent: str, field: SourceField) -> bool:
    if field == "html":
        return _markup(stored) == _markup(sent)
    return _collapse(stored) == _collapse(sent)


def _collapse(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _markup(text: str) -> str:
    """HTML with BookStack's save-time rewrites undone, entities decoded, whitespace collapsed."""
    text = _BOOKSTACK_ID.sub("", text)
    text = _SELF_CLOSING.sub(">", text)
    text = _SINGLE_QUOTED.sub(r'="\1"', text)
    return _collapse(html.unescape(text))
