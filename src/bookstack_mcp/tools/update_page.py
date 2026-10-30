"""The `update_page` tool: rename, replace content, retag, move or reorder a page."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.config import Toolset
from bookstack_mcp.editing.errors import PageStale
from bookstack_mcp.editing.guards import check_stale
from bookstack_mcp.editing.source import PageSource, SourceField
from bookstack_mcp.editing.verify import Checks
from bookstack_mcp.tools import CurrentDeps, Deps, tool
from bookstack_mcp.tools.create_page import TagInput
from bookstack_mcp.tools.page_write import PageChange, PageWriteResult, write_page

DEFAULT_CHANGELOG = "Updated via bookstack-mcp"


class PageLocation(BaseModel):
    """Where a page lives."""

    book_id: int
    chapter_id: int | None = Field(description="The chapter, or null at the book's top level.")


class UpdatedPage(BaseModel):
    """The outcome of update_page. Never contains the page content."""

    page_id: int
    name: str
    url: str = Field(description="The page's permanent link.")
    changed: list[str] = Field(
        description="The fields sent: name, markdown or html, tags, book_id or chapter_id, "
        "priority."
    )
    location: PageLocation | None = Field(
        description="Where the page is after a move; null when it was not moved or on a dry run."
    )
    tags: list[TagInput] | None = Field(
        description="The page's tags after the update; null when tags were not set."
    )
    dry_run: bool
    written: bool
    verified: bool | None = Field(
        description="Whether the re-read page holds every change; null when nothing was written."
    )
    updated_at: str | None = Field(description="Pass as expected_updated_at to later edits.")
    revision_count: int | None
    content: PageWriteResult | None = Field(
        description="The content replacement: source field, sizes and diff; null when no "
        "content was sent."
    )
    notes: list[str] = Field(default_factory=list)


@tool(Toolset.CORE, write=True)
async def update_page(
    page_id: Annotated[int, Field(description="The page's id.", ge=1)],
    name: Annotated[
        str | None, Field(description="New page name.", min_length=1, max_length=255)
    ] = None,
    markdown: Annotated[
        str | None,
        Field(description="New content as markdown; replaces the whole page.", min_length=1),
    ] = None,
    html: Annotated[
        str | None,
        Field(description="New content as HTML; replaces the whole page.", min_length=1),
    ] = None,
    tags: Annotated[
        list[TagInput] | None,
        Field(
            description="The complete new tag set; replaces ALL existing tags ([] removes them)."
        ),
    ] = None,
    book_id: Annotated[
        int | None, Field(description="Move the page to this book's top level.", ge=1)
    ] = None,
    chapter_id: Annotated[
        int | None, Field(description="Move the page into this chapter.", ge=1)
    ] = None,
    priority: Annotated[
        int | None, Field(description="Sort position within the book or chapter.", ge=0)
    ] = None,
    expected_updated_at: Annotated[
        str | None,
        Field(description="The page's updated_at from your read; a changed page is refused."),
    ] = None,
    allow_shrink: Annotated[
        bool, Field(description="Allow new content under half the page's current size.")
    ] = False,
    allow_format_change: Annotated[
        bool,
        Field(
            description="Allow html on a markdown page, or markdown on an HTML page, which "
            "converts how the page is stored."
        ),
    ] = False,
    dry_run: Annotated[bool, Field(description="Preview the change without writing.")] = False,
    changelog: Annotated[
        str | None,
        Field(
            description=f'Revision note (BookStack 26.09+). Default "{DEFAULT_CHANGELOG}".',
            min_length=1,
            max_length=180,
        ),
    ] = None,
    deps: Deps = CurrentDeps(),
) -> UpdatedPage:
    """Update a page: rename it, replace its whole content, replace its tags, move or reorder it.

    Give at least one of name, markdown or html, tags, book_id or chapter_id, priority;
    they are sent in one update. markdown or html replaces the entire content: for small
    changes use edit_page, to add content use append_to_page. Send the page's own format
    (read_page shows it); html on a markdown page or markdown on an HTML page converts
    the page and is refused unless allow_format_change is set. New content under half
    the current size is refused unless allow_shrink is set. tags REPLACES THE WHOLE TAG
    SET: to add one tag, read the page's tags first and send the full list. book_id moves
    the page to that book's top level, chapter_id into that chapter. Pass updated_at
    from your read as expected_updated_at so a page changed in the meantime is refused.
    The result lists what changed and whether the re-read page holds it (verified).
    """
    if markdown is not None and html is not None:
        raise ToolError("Give at most one of markdown or html, not both.")
    if book_id is not None and chapter_id is not None:
        raise ToolError("Give at most one of book_id or chapter_id, not both.")

    meta: dict[str, Any] = {}
    if name is not None:
        meta["name"] = name
    if tags is not None:
        meta["tags"] = [{"name": t.name, "value": t.value} for t in tags]
    if book_id is not None:
        meta["book_id"] = book_id
    if chapter_id is not None:
        meta["chapter_id"] = chapter_id
    if priority is not None:
        meta["priority"] = priority

    content_field: SourceField | None = None
    content_text = ""
    if markdown is not None:
        content_field, content_text = "markdown", markdown
    elif html is not None:
        content_field, content_text = "html", html

    if content_field is None and not meta:
        raise ToolError(
            "Nothing to update: give at least one of name, markdown, html, tags, book_id, "
            "chapter_id or priority."
        )

    changed = [k for k in ("name",) if k in meta]
    if content_field is not None:
        changed.append(content_field)
    changed += [k for k in ("tags", "book_id", "chapter_id", "priority") if k in meta]
    changelog = changelog or DEFAULT_CHANGELOG
    notes: list[str] = []

    if content_field is not None:
        field = content_field
        converted: list[SourceField] = []

        def compute(source: PageSource) -> PageChange:
            if field != source.field:
                if not allow_format_change:
                    raise ToolError(
                        f"Page {page_id} is stored as {source.field}; sending {field} would "
                        f"convert it to a {field} page. Send {source.field} instead, or set "
                        "allow_format_change=true to convert it."
                    )
                converted.append(source.field)
            return PageChange(content_text, Checks([content_text], []), field=field)

        content = await write_page(
            deps,
            page_id,
            compute,
            changelog=changelog,
            dry_run=dry_run,
            expected_updated_at=expected_updated_at,
            allow_shrink=allow_shrink,
            extra_body=meta,
        )
        notes += content.notes
        content = content.model_copy(update={"notes": []})
        if converted:
            notes.append(
                f"The page was converted from {converted[0]} to {field}. Its editor only "
                "changes when the API user's role has the editor-change permission."
            )
        if dry_run:
            return _result(page_id, deps, changed, content, None, None, notes, content=content)
        stored: Mapping[str, Any] | None = None
        verified = bool(content.verified)
        if set(meta) - {"name"}:
            stored = await deps.client.get_json(f"/pages/{page_id}")
            mismatches = _check_metadata(stored, meta)
            notes += mismatches
            verified = verified and not mismatches
        elif name is not None and content.name.strip() != name.strip():
            notes.append(f"name: sent {name!r}, but the page is named {content.name!r}.")
            verified = False
        return _result(
            page_id,
            deps,
            changed,
            content,
            stored,
            verified,
            notes,
            content=content,
            meta=meta,
        )

    page: Mapping[str, Any] | None = None
    if expected_updated_at is not None or dry_run:
        page = await deps.client.get_json(f"/pages/{page_id}")
        try:
            check_stale(str(page.get("updated_at")), expected_updated_at)
        except PageStale as exc:
            raise ToolError(
                f"Page {page_id} changed since it was read: its updated_at is "
                f"{exc.page_updated_at}, but expected_updated_at was "
                f"{exc.expected_updated_at}. Read the page again and decide whether the "
                "update still applies."
            ) from exc
    if page is not None and dry_run:
        notes.append("Dry run: nothing was written.")
        return _result(page_id, deps, changed, page, None, None, notes)

    body = dict(meta)
    if deps.features.changelog:
        body["changelog"] = changelog
    else:
        notes.append(
            f"changelog not sent: BookStack {deps.system.version} does not accept it "
            "(needs 26.09 or later)."
        )
    await deps.client.put_json(f"/pages/{page_id}", body)
    stored = await deps.client.get_json(f"/pages/{page_id}")
    mismatches = _check_metadata(stored, meta)
    notes += mismatches
    return _result(page_id, deps, changed, stored, stored, not mismatches, notes, meta=meta)


def _result(
    page_id: int,
    deps: Deps,
    changed: list[str],
    source: Mapping[str, Any] | PageWriteResult,
    stored: Mapping[str, Any] | None,
    verified: bool | None,
    notes: list[str],
    *,
    content: PageWriteResult | None = None,
    meta: Mapping[str, Any] | None = None,
) -> UpdatedPage:
    """Build the result from the latest page (or write result) and the re-read page."""
    if isinstance(source, PageWriteResult):
        name, updated_at, revisions = source.name, source.updated_at, source.revision_count
    else:
        name = str(source.get("name", ""))
        updated_at, revisions = source.get("updated_at"), source.get("revision_count")
    meta = meta or {}
    location = None
    tags = None
    if stored is not None:
        if "book_id" in meta or "chapter_id" in meta:
            location = PageLocation(
                book_id=int(stored["book_id"]), chapter_id=stored.get("chapter_id") or None
            )
        if "tags" in meta:
            tags = [
                TagInput(name=str(t.get("name", "")), value=str(t.get("value") or ""))
                for t in stored.get("tags") or []
            ]
    written = verified is not None  # verified is None exactly on a dry run
    return UpdatedPage(
        page_id=page_id,
        name=name,
        url=deps.links.page(page_id),
        changed=changed,
        location=location,
        tags=tags,
        dry_run=not written,
        written=written,
        verified=verified,
        updated_at=None if updated_at is None else str(updated_at),
        revision_count=revisions,
        content=content,
        notes=notes,
    )


def _check_metadata(page: Mapping[str, Any], meta: Mapping[str, Any]) -> list[str]:
    """Compare the re-read page with the metadata sent; one note per mismatch."""
    problems: list[str] = []
    if "name" in meta and str(page.get("name", "")).strip() != meta["name"].strip():
        problems.append(f"name: sent {meta['name']!r}, but the page is named {page.get('name')!r}.")
    if "tags" in meta and _tag_set(page.get("tags") or []) != _tag_set(meta["tags"]):
        problems.append("tags: the stored tags differ from the tags sent.")
    if "book_id" in meta and (page.get("book_id") != meta["book_id"] or page.get("chapter_id")):
        problems.append(
            f"book_id: the page was to move to the top level of book {meta['book_id']}, but "
            f"it is in book {page.get('book_id')}, chapter {page.get('chapter_id') or 'none'}."
        )
    if "chapter_id" in meta and page.get("chapter_id") != meta["chapter_id"]:
        problems.append(
            f"chapter_id: the page was to move into chapter {meta['chapter_id']}, but it is in "
            f"chapter {page.get('chapter_id') or 'none'}."
        )
    if "priority" in meta and page.get("priority") != meta["priority"]:
        problems.append(
            f"priority: sent {meta['priority']}, but the page's priority is {page.get('priority')}."
        )
    if problems:
        problems.append("Read the page to see what BookStack stored.")
    return problems


def _tag_set(tags: Sequence[Mapping[str, Any]]) -> Counter[tuple[str, str]]:
    return Counter(
        (str(t.get("name", "")).strip(), str(t.get("value") or "").strip()) for t in tags
    )
