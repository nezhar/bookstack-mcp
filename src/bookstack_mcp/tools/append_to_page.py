"""The `append_to_page` tool: insert content at the end of a page or a section, verified."""

from __future__ import annotations

from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import Field

from bookstack_mcp.config import Toolset
from bookstack_mcp.editing.append import Position, insert
from bookstack_mcp.editing.errors import SectionAmbiguous, SectionNotFound
from bookstack_mcp.editing.replace import ELLIPSIS
from bookstack_mcp.editing.source import PageSource
from bookstack_mcp.editing.verify import checks_for_insert
from bookstack_mcp.tools import CurrentDeps, Deps, tool
from bookstack_mcp.tools.page_write import ChangeContext, PageChange, PageWriteResult, write_page

DEFAULT_CHANGELOG = "Appended via bookstack-mcp"
CONTEXT_RADIUS = 120
INSERTED_SHOWN = 240
OUTLINE_HINT = 'Call read_page with format="outline" to see the headings, then retry.'


class AppendResult(PageWriteResult):
    """The outcome of an append. Never contains the page content."""

    position: Position = Field(description="Where the content was inserted.")
    section: str | None = Field(description="The section asked for, if any.")


@tool(Toolset.CORE, write=True)
async def append_to_page(
    page_id: Annotated[int, Field(description="The page's id.", ge=1)],
    content: Annotated[
        str,
        Field(
            description=(
                "The content to insert, in the page's source format (source_field in "
                "read_page): markdown for markdown pages, HTML for HTML pages. Plain text "
                "on an HTML page becomes <p> paragraphs."
            ),
            min_length=1,
        ),
    ],
    position: Annotated[
        Position,
        Field(
            description=(
                '"end" of the page; "section_end": end of the section, after its '
                'subsections; "after_heading": right below the section heading.'
            )
        ),
    ] = "end",
    section: Annotated[
        str | None,
        Field(
            description='Heading text of the section; required for "section_end" and '
            '"after_heading", not allowed with "end".',
            min_length=1,
        ),
    ] = None,
    separator: Annotated[
        str | None,
        Field(
            description=(
                "Text between the existing content and the new content. Default: a blank "
                "line for markdown, a newline for HTML."
            )
        ),
    ] = None,
    dry_run: Annotated[
        bool, Field(description="Preview the sizes and the diff without writing.")
    ] = False,
    expected_updated_at: Annotated[
        str | None,
        Field(description="The page's updated_at from your read; a changed page is refused."),
    ] = None,
    changelog: Annotated[
        str,
        Field(
            description=f'Revision note (BookStack 26.09+). Default "{DEFAULT_CHANGELOG}".',
            min_length=1,
            max_length=180,
        ),
    ] = DEFAULT_CHANGELOG,
    deps: Deps = CurrentDeps(),
) -> AppendResult:
    """Add content to a page, at its end or at a section, then verify the write.

    Write content in the page's source format, as read_page reports it in source_field:
    markdown for markdown pages, HTML for HTML pages; plain text on an HTML page is
    wrapped in <p> paragraphs. position "end" appends to the page; "section_end" adds to
    the end of the named section, after its subsections and before the next heading of
    the same or a higher level; "after_heading" puts it right below the heading. Name the
    section by its heading text; read_page with format="outline" lists the headings.
    Existing content is never changed. The result has sizes, a diff, where the content
    landed and whether the re-read page holds it (verified); it never contains the page.
    """

    def compute(source: PageSource) -> PageChange:
        try:
            text = insert(
                source.text,
                content,
                source.field,
                position=position,
                section=section,
                separator=separator,
            )
        except (SectionNotFound, SectionAmbiguous) as exc:
            raise ToolError(f"{exc} {OUTLINE_HINT}") from exc
        landed = _landing_context(source.text, text)
        return PageChange(text, checks_for_insert(content, source.field), [landed])

    result = await write_page(
        deps,
        page_id,
        compute,
        changelog=changelog,
        dry_run=dry_run,
        expected_updated_at=expected_updated_at,
    )
    return AppendResult(**result.model_dump(), position=position, section=section)


def _landing_context(before: str, after: str) -> ChangeContext:
    """Show the inserted text with the source around it, the middle cut when long.

    ``insert`` only adds text, so the insertion starts where the two texts first differ.
    """
    start = 0
    limit = min(len(before), len(after))
    while start < limit and before[start] == after[start]:
        start += 1
    end = start + len(after) - len(before)
    inserted = after[start:end]
    if len(inserted) > INSERTED_SHOWN:
        half = INSERTED_SHOWN // 2
        inserted = inserted[:half] + ELLIPSIS + inserted[-half:]
    lo = max(0, start - CONTEXT_RADIUS)
    hi = min(len(after), end + CONTEXT_RADIUS)
    context = (
        (ELLIPSIS if lo > 0 else "")
        + after[lo:start]
        + inserted
        + after[end:hi]
        + (ELLIPSIS if hi < len(after) else "")
    )
    return ChangeContext(index=0, context=context)
