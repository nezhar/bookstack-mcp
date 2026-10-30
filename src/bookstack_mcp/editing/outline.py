"""Heading outline of markdown or HTML page source, and section lookup."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bookstack_mcp.editing._text import plain_text
from bookstack_mcp.editing.errors import SectionAmbiguous, SectionNotFound
from bookstack_mcp.editing.source import SourceField

# A closing run of '#' only counts when preceded by whitespace, so "# C#" keeps "C#".
_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HTML_HEADING = re.compile(r"<h([1-6])\b([^>]*)>([\s\S]*?)</h\1\s*>", re.IGNORECASE)
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
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        fence_match = _MD_FENCE.match(stripped)
        if fence is None:
            if fence_match:
                fence = fence_match.group(1)
            elif heading := _MD_HEADING.match(stripped):
                found.append((len(heading.group(1)), plain_text(heading.group(2)), None, offset))
        elif (
            fence_match
            and fence_match.group(1)[0] == fence[0]
            and len(fence_match.group(1)) >= len(fence)
            and not stripped[fence_match.end() :].strip()
        ):
            fence = None
        offset += len(line)
    return found


def _html_headings(text: str) -> list[_RawHeading]:
    found: list[_RawHeading] = []
    for match in _HTML_HEADING.finditer(text):
        id_match = _HTML_ID.search(match.group(2))
        anchor = next((g for g in id_match.groups() if g is not None), None) if id_match else None
        found.append((int(match.group(1)), plain_text(match.group(3)), anchor, match.start()))
    return found
