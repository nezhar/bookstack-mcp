# Behaviour adapted from pnocera/bookstack-mcp-server v2.1.1 (src/utils/page-content.ts),
# used under the MIT License:
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

"""Literal text search in page source with context excerpts."""

import re
from dataclasses import dataclass

from bookstack_mcp.editing.errors import InvalidArgument

CONTEXT_MIN, CONTEXT_MAX = 1, 2000
MAX_MATCHES_MIN, MAX_MATCHES_MAX = 1, 50
ELLIPSIS = "…"


@dataclass(frozen=True, slots=True)
class GrepMatch:
    """One occurrence: its offset in the text, the matched text, and an excerpt around it."""

    offset: int
    match: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class GrepResult:
    """Up to ``max_matches`` matches; ``total`` counts every match in the text."""

    matches: list[GrepMatch]
    total: int
    truncated: bool


def grep(
    text: str,
    query: str,
    *,
    case_sensitive: bool = False,
    context: int = 200,
    max_matches: int = 10,
) -> GrepResult:
    """Find non-overlapping literal occurrences of ``query`` in ``text``.

    The query is never treated as a regular expression. Each excerpt runs from
    ``context`` characters before the match to ``context`` after it, marked with
    ``…`` where it was cut.
    """
    if not query:
        raise InvalidArgument("The search query must not be empty.")
    if not CONTEXT_MIN <= context <= CONTEXT_MAX:
        raise InvalidArgument(f"context must be between {CONTEXT_MIN} and {CONTEXT_MAX}.")
    if not MAX_MATCHES_MIN <= max_matches <= MAX_MATCHES_MAX:
        raise InvalidArgument(
            f"max_matches must be between {MAX_MATCHES_MIN} and {MAX_MATCHES_MAX}."
        )
    pattern = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
    matches: list[GrepMatch] = []
    total = 0
    for found in pattern.finditer(text):
        total += 1
        if len(matches) < max_matches:
            start = max(0, found.start() - context)
            end = min(len(text), found.end() + context)
            excerpt = (
                (ELLIPSIS if start > 0 else "")
                + text[start:end]
                + (ELLIPSIS if end < len(text) else "")
            )
            matches.append(GrepMatch(found.start(), found.group(0), excerpt))
    return GrepResult(matches, total, total > len(matches))
