"""The `edit_page` tool: literal find-and-replace on a page's stored source, verified."""

from __future__ import annotations

from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.config import Toolset
from bookstack_mcp.editing.errors import AnchorAmbiguous
from bookstack_mcp.editing.replace import apply_edits
from bookstack_mcp.editing.source import PageSource
from bookstack_mcp.editing.verify import checks_for_edits
from bookstack_mcp.tools import CurrentDeps, Deps, tool
from bookstack_mcp.tools.page_write import ChangeContext, PageChange, PageWriteResult, write_page

MAX_EDITS = 20


class PageEdit(BaseModel):
    """One literal replacement in the page's stored source."""

    old_string: str = Field(
        min_length=1,
        description="Exact text in the stored source (markdown or raw HTML) to replace.",
    )
    new_string: str = Field(description="The replacement text, in the same format.")
    replace_all: bool = Field(
        default=False,
        description="Replace every occurrence; otherwise old_string must occur exactly once.",
    )


@tool(Toolset.CORE, write=True)
async def edit_page(
    page_id: Annotated[int, Field(description="The page's id.", ge=1)],
    edits: Annotated[
        list[PageEdit],
        Field(
            description="Replacements applied in order, each to the result of the previous one.",
            min_length=1,
            max_length=MAX_EDITS,
        ),
    ],
    dry_run: Annotated[
        bool, Field(description="Preview the sizes and the diff without writing.")
    ] = False,
    expected_updated_at: Annotated[
        str | None,
        Field(description="The page's updated_at from your read; a changed page is refused."),
    ] = None,
    allow_shrink: Annotated[
        bool, Field(description="Allow a result under half the page's current size.")
    ] = False,
    changelog: Annotated[
        str | None,
        Field(
            description=(
                "Revision note (BookStack 26.09+). "
                'Default "Edited via bookstack-mcp: <n> change(s)".'
            ),
            min_length=1,
            max_length=180,
        ),
    ] = None,
    deps: Deps = CurrentDeps(),
) -> PageWriteResult:
    """Edit a page by exact find-and-replace on its stored source, then verify the write.

    Anchors (old_string) must match the page's stored source exactly: markdown for
    markdown pages, raw HTML for HTML pages, not the rendered text. Take them from
    read_page with grep or format="source". Each old_string must occur once unless
    replace_all is set; all edits apply or none do. Use dry_run first on anything
    non-trivial, and pass updated_at from your read as expected_updated_at so a page
    changed in the meantime is refused. A result under half the page's size is refused
    unless allow_shrink is set. The result has sizes, a diff, each edit's context and
    whether the re-read page holds the change (verified); it never contains the page.
    """

    def compute(source: PageSource) -> PageChange:
        try:
            result = apply_edits(source.text, edits)
        except AnchorAmbiguous as exc:
            shown = "\n".join(f"{n}. {m!r}" for n, m in enumerate(exc.matches, 1))
            raise ToolError(
                f"Edit {exc.edit_index}: old_string occurs {exc.count} times in the stored "
                f"source. First matches:\n{shown}\n{exc.hint}"
            ) from exc
        contexts = [
            ChangeContext(
                index=a.index, occurrences_replaced=a.occurrences_replaced, context=a.context
            )
            for a in result.applied
        ]
        checks = checks_for_edits(source.text, result.text, edits, source.field)
        return PageChange(result.text, checks, contexts)

    return await write_page(
        deps,
        page_id,
        compute,
        changelog=changelog or f"Edited via bookstack-mcp: {len(edits)} change(s)",
        dry_run=dry_run,
        expected_updated_at=expected_updated_at,
        allow_shrink=allow_shrink,
        rebuild="anchors",
    )
