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

"""Choose which field of a BookStack page holds its editable source."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

SourceField = Literal["markdown", "html"]


@dataclass(frozen=True, slots=True)
class PageSource:
    """The editable source of a page.

    ``field`` names what to write back: ``"markdown"`` means write ``markdown``;
    ``"html"`` means the text came from ``raw_html`` and is written back as ``html``.
    """

    field: SourceField
    text: str
    editor: str


def select_source(page: Mapping[str, Any]) -> PageSource:
    """Pick the editable source of a page as returned by ``GET /api/pages/{id}``.

    BookStack keeps ``markdown`` only while a page is saved as markdown and clears it
    on any HTML save, so a non-empty ``markdown`` wins even when ``editor`` says
    ``wysiwyg``. The rendered ``html`` field is never used: it has page includes
    expanded, and writing it back would destroy them.
    """
    markdown = page.get("markdown") or ""
    raw_html = page.get("raw_html") or ""
    editor = page.get("editor") or ""
    if markdown:
        return PageSource("markdown", markdown, editor)
    if not raw_html and editor == "markdown":
        return PageSource("markdown", "", editor)
    return PageSource("html", raw_html, editor)
