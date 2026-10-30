"""The `browse` tool: walk the library from shelves and books down to chapters and pages."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.client import Links
from bookstack_mcp.config import Toolset
from bookstack_mcp.tools import CurrentDeps, Deps, tool

__all__ = [
    "BrowseItem",
    "BrowseListing",
    "BrowseResult",
    "ContentNode",
    "Tag",
    "browse",
    "escape_like",
]


class BrowseItem(BaseModel):
    """A shelf, book or page in a list."""

    id: int
    name: str
    url: str
    updated_at: str | None = None


class BrowseListing(BaseModel):
    """One page of a list, with the total across all pages."""

    items: list[BrowseItem]
    total: int


class ContentNode(BaseModel):
    """A chapter (with its pages) or a page in a book's contents."""

    type: Literal["chapter", "page"]
    id: int
    name: str
    url: str
    pages: list[ContentNode] | None = None
    """A chapter's pages in BookStack's order; `None` for pages."""


class Tag(BaseModel):
    name: str
    value: str = ""


class BrowseResult(BaseModel):
    """What `browse` returns. Which fields are set depends on `level`."""

    level: Literal["library", "shelf", "book", "chapter"]
    id: int | None = None
    name: str | None = None
    url: str | None = None
    book_id: int | None = None
    """The chapter's book (chapter level)."""
    tags: list[Tag] | None = None
    """The book's tags (book level)."""
    shelves: BrowseListing | None = None
    """Shelves sorted by name (library level)."""
    books: BrowseListing | None = None
    """Books sorted by name (library level), or the shelf's books (shelf level)."""
    contents: list[ContentNode] | None = None
    """The book's chapters and loose pages in BookStack's order (book level)."""
    pages: list[BrowseItem] | None = None
    """The chapter's pages in BookStack's order (chapter level)."""
    next: str
    """How to go deeper or page on."""


def escape_like(term: str) -> str:
    """Escape `term` for a SQL LIKE pattern: backslash, `%` and `_` get a backslash."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _item(raw: dict[str, Any], url: str, *, with_updated: bool = False) -> BrowseItem:
    updated = raw.get("updated_at") if with_updated else None
    return BrowseItem(
        id=int(raw["id"]),
        name=str(raw.get("name", "")),
        url=url,
        updated_at=updated if isinstance(updated, str) else None,
    )


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _book_url(links: Links, book: dict[str, Any]) -> str:
    return Links.prefer(book, links.book(str(book.get("slug", ""))))


def _content_node(links: Links, raw: dict[str, Any], book_slug: str) -> ContentNode | None:
    if raw.get("draft"):
        return None
    kind = raw.get("type")
    if kind == "chapter":
        url = Links.prefer(raw, links.chapter(book_slug, str(raw.get("slug", ""))))
        pages = [
            node
            for page in _dicts(raw.get("pages"))
            if (node := _content_node(links, {**page, "type": "page"}, book_slug)) is not None
        ]
        return ContentNode(
            type="chapter", id=int(raw["id"]), name=str(raw.get("name", "")), url=url, pages=pages
        )
    if kind == "page":
        url = Links.prefer(raw, links.page(int(raw["id"])))
        return ContentNode(type="page", id=int(raw["id"]), name=str(raw.get("name", "")), url=url)
    return None


async def _library(deps: Deps, name_contains: str | None, offset: int, count: int) -> BrowseResult:
    filters = {"name:like": f"%{escape_like(name_contains)}%"} if name_contains else None
    shelves, books = await asyncio.gather(
        deps.client.list_page("/shelves", count=count, offset=offset, sort="name", filters=filters),
        deps.client.list_page("/books", count=count, offset=offset, sort="name", filters=filters),
    )
    links = deps.links
    next_steps = [
        "Call browse with shelf_id for a shelf's books, or with book_id for a book's "
        "chapters and pages."
    ]
    if offset + count < max(shelves.total, books.total):
        next_steps.append(f"More results: call browse with offset={offset + count}.")
    return BrowseResult(
        level="library",
        shelves=BrowseListing(
            items=[
                _item(s, Links.prefer(s, links.shelf(str(s.get("slug", "")))), with_updated=True)
                for s in shelves.data
            ],
            total=shelves.total,
        ),
        books=BrowseListing(
            items=[_item(b, _book_url(links, b), with_updated=True) for b in books.data],
            total=books.total,
        ),
        next=" ".join(next_steps),
    )


async def _shelf(deps: Deps, shelf_id: int) -> BrowseResult:
    shelf = await deps.client.get_json(f"/shelves/{shelf_id}")
    links = deps.links
    books = [_item(b, _book_url(links, b)) for b in _dicts(shelf.get("books"))]
    return BrowseResult(
        level="shelf",
        id=int(shelf["id"]),
        name=str(shelf.get("name", "")),
        url=Links.prefer(shelf, links.shelf(str(shelf.get("slug", "")))),
        books=BrowseListing(items=books, total=len(books)),
        next="Call browse with book_id for a book's chapters and pages.",
    )


async def _book(deps: Deps, book_id: int) -> BrowseResult:
    book = await deps.client.get_json(f"/books/{book_id}")
    slug = str(book.get("slug", ""))
    contents = [
        node
        for raw in _dicts(book.get("contents"))
        if (node := _content_node(deps.links, raw, slug)) is not None
    ]
    tags = [
        Tag(name=str(t.get("name", "")), value=str(t.get("value") or ""))
        for t in _dicts(book.get("tags"))
    ]
    return BrowseResult(
        level="book",
        id=int(book["id"]),
        name=str(book.get("name", "")),
        url=_book_url(deps.links, book),
        tags=tags,
        contents=contents,
        next="Call browse with chapter_id for a chapter's pages, or read_page with page_id "
        "for a page's content.",
    )


async def _chapter(deps: Deps, chapter_id: int) -> BrowseResult:
    chapter = await deps.client.get_json(f"/chapters/{chapter_id}")
    links = deps.links
    pages = [
        _item(p, links.page(int(p["id"])))
        for p in _dicts(chapter.get("pages"))
        if not p.get("draft")
    ]
    book_slug = str(chapter.get("book_slug", ""))
    book_id = chapter.get("book_id")
    return BrowseResult(
        level="chapter",
        id=int(chapter["id"]),
        name=str(chapter.get("name", "")),
        url=Links.prefer(chapter, links.chapter(book_slug, str(chapter.get("slug", "")))),
        book_id=int(book_id) if book_id is not None else None,
        pages=pages,
        next="Call read_page with page_id for a page's content, or browse with book_id for "
        "the whole book.",
    )


@tool(Toolset.CORE, title="Browse the library")
async def browse(
    shelf_id: Annotated[int | None, Field(description="List this shelf's books.", ge=1)] = None,
    book_id: Annotated[
        int | None,
        Field(description="Show this book's chapters and pages as a tree.", ge=1),
    ] = None,
    chapter_id: Annotated[int | None, Field(description="List this chapter's pages.", ge=1)] = None,
    name_contains: Annotated[
        str | None,
        Field(
            description="Only shelves and books whose name contains this text. Only without an id.",
            min_length=1,
            max_length=255,
        ),
    ] = None,
    offset: Annotated[
        int, Field(description="Skip this many shelves and books. Only without an id.", ge=0)
    ] = 0,
    count: Annotated[
        int,
        Field(description="Shelves and books per list. Only without an id.", ge=1, le=100),
    ] = 50,
    deps: Deps = CurrentDeps(),
) -> BrowseResult:
    """Browse BookStack's structure without reading page content.

    Without an id: the shelves and books, sorted by name, paged with offset and count.
    With shelf_id: the shelf's books. With book_id: the book's tags and its chapters and
    pages in order. With chapter_id: the chapter's pages. Pass at most one id. Each result's
    `next` says how to go deeper.
    """
    ids = {"shelf_id": shelf_id, "book_id": book_id, "chapter_id": chapter_id}
    given = [name for name, value in ids.items() if value is not None]
    if len(given) > 1:
        raise ToolError(
            f"browse takes at most one id, got {' and '.join(given)}; pass only one of "
            "shelf_id, book_id or chapter_id."
        )
    if given and name_contains is not None:
        raise ToolError(
            "name_contains only filters the library listing; call browse without an id to "
            "use it, or drop name_contains."
        )
    if shelf_id is not None:
        return await _shelf(deps, shelf_id)
    if book_id is not None:
        return await _book(deps, book_id)
    if chapter_id is not None:
        return await _chapter(deps, chapter_id)
    return await _library(deps, name_contains, offset, count)
