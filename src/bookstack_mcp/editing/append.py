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

"""Insert content at the end of a page, or at the end or start of a named section."""

import re
from typing import Literal

from bookstack_mcp.editing.errors import InvalidArgument
from bookstack_mcp.editing.outline import Heading, build_outline, find_section
from bookstack_mcp.editing.source import SourceField

Position = Literal["end", "section_end", "after_heading"]

_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>|<!--")
_BLANK_LINE = re.compile(r"\n[ \t]*(?:\r?\n)+")


def insert(
    text: str,
    content: str,
    field: SourceField,
    *,
    position: Position = "end",
    section: str | None = None,
    separator: str | None = None,
) -> str:
    """Return ``text`` with ``content`` inserted.

    ``position`` is ``"end"`` (end of the page; ``section`` must be ``None``),
    ``"section_end"`` (end of the named section including its subsections, before
    the next heading of the same or a higher level) or ``"after_heading"`` (right
    after the heading line, or after the closing ``</hN>``). An end position first
    steps back over trailing whitespace so the content stays inside the section.

    ``separator`` goes between the existing text and the content; it defaults to
    ``"\\n\\n"`` for markdown and ``"\\n"`` for HTML. On an HTML page, content with
    no HTML tag is treated as plain text and wrapped in escaped ``<p>`` paragraphs.
    Raises ``InvalidArgument``, ``SectionNotFound`` or ``SectionAmbiguous``.
    """
    if not content.strip():
        raise InvalidArgument("content must not be empty.")
    if position == "end":
        if section is not None:
            raise InvalidArgument(
                'section is only used with position "section_end" or "after_heading".'
            )
    elif position in ("section_end", "after_heading"):
        if not section:
            raise InvalidArgument(f'position "{position}" needs a section name.')
    else:
        raise InvalidArgument(
            f'position must be "end", "section_end" or "after_heading", not {position!r}.'
        )
    if separator is None:
        separator = "\n\n" if field == "markdown" else "\n"
    content = prepare_content(content, field)

    if section is None:
        at = _back_over_space(text, 0, len(text))
    else:
        heading = find_section(build_outline(text, field), section)
        heading_end = _heading_end(text, heading, field)
        if position == "after_heading":
            at = heading_end
        else:
            at = _back_over_space(text, heading_end, heading.offset + heading.section_length)
    before, after = text[:at], text[at:]
    return before + (separator if before else "") + content + after


def prepare_content(content: str, field: SourceField) -> str:
    """Return the content as it will be inserted: plain text on an HTML page becomes ``<p>``."""
    if field == "html" and not _HTML_TAG.search(content):
        return plain_text_to_html(content)
    return content


def plain_text_to_html(content: str) -> str:
    """Split plain text on blank lines, escape ``&<>`` and wrap each paragraph in ``<p>``."""
    paragraphs = [p.strip() for p in _BLANK_LINE.split(content.replace("\r\n", "\n"))]
    return "\n".join(
        "<p>" + p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</p>"
        for p in paragraphs
        if p
    )


def _heading_end(text: str, heading: Heading, field: SourceField) -> int:
    """Offset just past the heading: end of its line (before the newline), or ``</hN>``."""
    if field == "markdown":
        newline = text.find("\n", heading.offset)
        end = len(text) if newline == -1 else newline
        return end - 1 if end > heading.offset and text[end - 1] == "\r" else end
    closing = re.compile(rf"</h{heading.level}\s*>", re.IGNORECASE).search(text, heading.offset)
    # The outline only lists headings whose closing tag exists.
    assert closing is not None  # noqa: S101
    return closing.end()


def _back_over_space(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1].isspace():
        end -= 1
    return end
