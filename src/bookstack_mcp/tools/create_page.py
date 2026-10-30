"""The `create_page` tool: create a page in a book or chapter, optionally from a template."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.config import Toolset
from bookstack_mcp.editing.source import SourceField, select_source
from bookstack_mcp.tools import CurrentDeps, Deps, tool

DEFAULT_CHANGELOG = "Created via bookstack-mcp"


class TagInput(BaseModel):
    """A tag to set on the page."""

    name: str = Field(description="Tag name.", min_length=1, max_length=255)
    value: str = Field(default="", description="Tag value; empty for a name-only tag.")


class CreatedPage(BaseModel):
    page_id: int
    name: str
    url: str = Field(description="The page's permanent link.")
    book_id: int
    chapter_id: int | None = Field(description="The chapter, or null for a page in a book.")
    editor: str = Field(description="The editor BookStack opens the page in.")
    source_field: SourceField = Field(
        description="Where the page's source is stored: markdown, or html (raw_html)."
    )
    updated_at: str = Field(description="Pass as expected_updated_at to later edits.")
    notes: list[str] = Field(default_factory=list, description="Things worth telling the user.")


@tool(Toolset.CORE, write=True)
async def create_page(
    name: Annotated[str, Field(description="The page name.", min_length=1, max_length=255)],
    book_id: Annotated[
        int | None,
        Field(description="Book to create the page in. Give this or chapter_id.", ge=1),
    ] = None,
    chapter_id: Annotated[
        int | None,
        Field(description="Chapter to create the page in. Give this or book_id.", ge=1),
    ] = None,
    markdown: Annotated[
        str | None,
        Field(description="Page content as markdown (the default format).", min_length=1),
    ] = None,
    html: Annotated[
        str | None,
        Field(description="Page content as HTML, instead of markdown.", min_length=1),
    ] = None,
    template_page_id: Annotated[
        int | None,
        Field(
            description="Page whose stored content to copy when no markdown or html is given.",
            ge=1,
        ),
    ] = None,
    tags: Annotated[
        list[TagInput] | None, Field(description="Tags to set, as name and value.")
    ] = None,
    priority: Annotated[
        int | None, Field(description="Sort position within the book or chapter.", ge=0)
    ] = None,
    changelog: Annotated[
        str | None,
        Field(
            description="Revision note (BookStack 26.09+). Defaults to 'Created via "
            "bookstack-mcp'.",
            min_length=1,
            max_length=180,
        ),
    ] = None,
    deps: Deps = CurrentDeps(),
) -> CreatedPage:
    """Create a page in a book or chapter.

    Give exactly one of book_id or chapter_id, and the content as markdown (preferred) or
    html. Without content, template_page_id is required and that page's stored content is
    copied. Images embedded as base64 data URLs (data:image/png;base64,...) are turned into
    gallery images by BookStack itself. Returns the new page's id, link and updated_at.
    """
    if (book_id is None) == (chapter_id is None):
        raise ToolError("Give exactly one of book_id or chapter_id.")
    if markdown is not None and html is not None:
        raise ToolError("Give at most one of markdown or html, not both.")

    notes: list[str] = []
    field: SourceField
    content: str
    if markdown is not None:
        field, content = "markdown", markdown
    elif html is not None:
        field, content = "html", html
    elif template_page_id is not None:
        template = await deps.client.get_json(f"/pages/{template_page_id}")
        source = select_source(template)
        if not source.text:
            raise ToolError(
                f"Template page {template_page_id} has no content; "
                "give markdown or html, or another template_page_id."
            )
        field, content = source.field, source.text
    else:
        raise ToolError(
            "No content given: pass markdown (preferred) or html, "
            "or template_page_id to copy a template page."
        )
    if template_page_id is not None and (markdown is not None or html is not None):
        notes.append(f"Template page {template_page_id} was not used, because content was given.")

    body: dict[str, Any] = {"name": name, field: content}
    if book_id is not None:
        body["book_id"] = book_id
    else:
        body["chapter_id"] = chapter_id
    if tags:
        body["tags"] = [{"name": t.name, "value": t.value} for t in tags]
    if priority is not None:
        body["priority"] = priority
    if deps.features.changelog:
        body["changelog"] = changelog or DEFAULT_CHANGELOG
    elif changelog is not None:
        notes.append("The changelog was not saved: revision notes need BookStack 26.09 or later.")

    page = await deps.client.post_json("/pages", body)

    editor = page.get("editor") or ""
    if field == "markdown" and editor != "markdown":
        notes.append(
            "The content was sent as markdown, but the page's editor is "
            f"{editor or 'not markdown'}: the AI user's role lacks the editor-change "
            "permission, so people will open the page in the WYSIWYG editor."
        )

    return CreatedPage(
        page_id=page["id"],
        name=page["name"],
        url=deps.links.page(page["id"]),
        book_id=page["book_id"],
        chapter_id=page.get("chapter_id") or None,
        editor=editor,
        source_field=select_source(page).field,
        updated_at=str(page["updated_at"]),
        notes=notes,
    )
