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

"""Preflight checks run before a page write."""

from bookstack_mcp.editing.errors import PageStale, ShrinkRefused

SHRINK_RATIO = 0.5


def check_shrink(before: str, after: str, allow_shrink: bool = False) -> None:
    """Refuse a result under half the original size unless ``allow_shrink``.

    Guards against an anchor that accidentally swallows most of a page. Raises
    ``ShrinkRefused`` with both sizes in characters.
    """
    if not allow_shrink and len(after) < SHRINK_RATIO * len(before):
        raise ShrinkRefused(len(before), len(after))


def check_stale(page_updated_at: str, expected_updated_at: str | None) -> None:
    """Raise ``PageStale`` when the page's ``updated_at`` differs from the expected one.

    The values are compared as strings, exactly as BookStack returned them; no
    timestamp parsing. ``None`` skips the check. This is a preflight, not a lock:
    BookStack has no conditional update, so a write can still race another one.
    """
    if expected_updated_at is not None and page_updated_at != expected_updated_at:
        raise PageStale(page_updated_at, expected_updated_at)
