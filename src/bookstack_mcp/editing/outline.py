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

"""Heading outline of markdown or HTML page source, and section lookup."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bookstack_mcp.editing._text import plain_text
from bookstack_mcp.editing.errors import SectionAmbiguous, SectionNotFound
from bookstack_mcp.editing.source import SourceField

# A closing run of '#' only counts when preceded by whitespace, so "# C#" keeps "C#".
_MD_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
# A setext underline turns the paragraph line above it into a level 1 (=) or 2 (-) heading.
_MD_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
# Lines that are not paragraph text, so an underline after them is not a setext heading.
_MD_NOT_PARAGRAPH = re.compile(r"^(?: {4}|\t| {0,3}(?:[-*+>|]|\d{1,9}[.)])(?:[ \t]|$))")
_MD_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_MD_MASKED = re.compile("\x00(\\d+)\x00")
_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HTML_HEADING = re.compile(r"<h([1-6])\b([^>]*)>([\s\S]*?)</h\1\s*>", re.IGNORECASE)
# Only \n (and \r\n) end a line; str.splitlines would also split on U+2028, \x0c and others.
_LINE = re.compile(r"[^\n]*\n|[^\n]+")
_HTML_ID = re.compile(r"""(?:^|\s)id\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Heading:
    """One heading in the source text.

    ``offset`` is where the heading starts (the line start in markdown, the ``<h``
    in HTML). ``length`` runs to the next heading of any level; ``section_length``
    runs to the next heading of the same or a higher level, or to the end.
    """

    level: int
    text: str
    anchor: str | None
    offset: int
    length: int
    section_length: int


def build_outline(text: str, field: SourceField) -> list[Heading]:
    """List the headings of ``text``, parsed as markdown or HTML according to ``field``."""
    raw = _markdown_headings(text) if field == "markdown" else _html_headings(text)
    headings: list[Heading] = []
    for i, (level, title, anchor, offset) in enumerate(raw):
        next_any = raw[i + 1][3] if i + 1 < len(raw) else len(text)
        section_end = next(
            (later[3] for later in raw[i + 1 :] if later[0] <= level),
            len(text),
        )
        headings.append(
            Heading(level, title, anchor, offset, next_any - offset, section_end - offset)
        )
    return headings


def find_section(outline: Sequence[Heading], name: str) -> Heading:
    """Find a heading by exact case-insensitive text, falling back to a unique substring.

    Raises ``SectionNotFound`` when nothing matches and ``SectionAmbiguous`` when
    several headings contain ``name`` and none matches exactly.
    """
    wanted = name.strip().casefold()
    for heading in outline:
        if heading.text.casefold() == wanted:
            return heading
    partial = [h for h in outline if wanted in h.text.casefold()]
    if not partial:
        raise SectionNotFound(name, [h.text for h in outline])
    if len(partial) > 1:
        raise SectionAmbiguous(name, [h.text for h in partial])
    return partial[0]


_RawHeading = tuple[int, str, str | None, int]


def _markdown_headings(text: str) -> list[_RawHeading]:
    found: list[_RawHeading] = []
    fence: str | None = None
    offset = 0
    # The previous line and its offset while it could be the text of a setext heading.
    paragraph: tuple[str, int] | None = None
    for line in _LINE.findall(text):
        stripped = line.rstrip("\r\n")
        fence_match = _MD_FENCE.match(stripped)
        candidate = paragraph
        paragraph = None
        if fence is None:
            if fence_match:
                fence = fence_match.group(1)
            elif heading := _MD_HEADING.match(stripped):
                found.append((len(heading.group(1)), _heading_text(heading.group(2)), None, offset))
            elif candidate and (underline := _MD_SETEXT.match(stripped)):
                level = 1 if underline.group(1)[0] == "=" else 2
                found.append((level, _heading_text(candidate[0]), None, candidate[1]))
            elif stripped.strip() and not _MD_NOT_PARAGRAPH.match(stripped):
                paragraph = (stripped, offset)
        elif (
            fence_match
            and fence_match.group(1)[0] == fence[0]
            and len(fence_match.group(1)) >= len(fence)
            and not stripped[fence_match.end() :].strip()
        ):
            fence = None
        offset += len(line)
    return found


def _heading_text(source: str) -> str:
    """Markdown heading text: tags stripped and entities decoded, except inside code spans."""
    spans: list[str] = []

    def mask(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    text = plain_text(_MD_CODE_SPAN.sub(mask, source))
    return _MD_MASKED.sub(lambda m: spans[int(m.group(1))], text)


def _html_headings(text: str) -> list[_RawHeading]:
    found: list[_RawHeading] = []
    for match in _HTML_HEADING.finditer(text):
        id_match = _HTML_ID.search(match.group(2))
        anchor = next((g for g in id_match.groups() if g is not None), None) if id_match else None
        found.append((int(match.group(1)), plain_text(match.group(3)), anchor, match.start()))
    return found
