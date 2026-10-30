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
