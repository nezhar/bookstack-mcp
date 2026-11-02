"""The `list_tags` tool: tag names, or the values of one tag name, by usage."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.client.http import list_params
from bookstack_mcp.config import Toolset
from bookstack_mcp.tools import CurrentDeps, Deps, tool


class TagName(BaseModel):
    """One tag name with how often and where it is used."""

    name: str
    values: int = Field(description="Number of distinct values this tag name has.")
    usages: int
    page_count: int
    chapter_count: int
    book_count: int
    shelf_count: int


class TagValue(BaseModel):
    """One value of a tag name with how often and where it is used."""

    value: str
    usages: int
    page_count: int
    chapter_count: int
    book_count: int
    shelf_count: int


class TagList(BaseModel):
    """Tag names (without `name`) or the values of one tag name (with `name`)."""

    name: str | None = Field(description="The tag name whose values are listed, if any.")
    names: list[TagName] = Field(default_factory=list)
    values: list[TagValue] = Field(default_factory=list)
    total: int = Field(description="Matching rows across all pages.")
    offset: int


def _int(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    return value if isinstance(value, int) else 0


def _counts(row: dict[str, Any]) -> dict[str, int]:
    keys = ("usages", "page_count", "chapter_count", "book_count", "shelf_count")
    return {key: _int(row, key) for key in keys}


def _rows(body: Any) -> tuple[list[dict[str, Any]], int]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ToolError("BookStack's tags endpoint did not return a list; check the instance.")
    rows = [row for row in data if isinstance(row, dict)]
    total = body.get("total")
    return rows, total if isinstance(total, int) else len(rows)


@tool(Toolset.CORE, needs={"tags_api"})
async def list_tags(
    name: Annotated[
        str | None,
        Field(
            description="A tag name (exact) to list its values. Omit to list tag names.",
            min_length=1,
            max_length=255,
        ),
    ] = None,
    name_contains: Annotated[
        str | None,
        Field(
            description="Only tag names containing this text. Only without `name`.",
            min_length=1,
            max_length=255,
        ),
    ] = None,
    count: Annotated[int, Field(description="Rows per page.", ge=1, le=500)] = 50,
    offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
    deps: Deps = CurrentDeps(),
) -> TagList:
    """List the tags in use, most used first.

    Without `name`: tag names, each with its number of distinct values, total usages and
    usage counts per item type (pages, chapters, books, shelves). `name_contains` narrows
    the names. With `name`: the values of that tag name, with the same counts.
    """
    if name is not None and name_contains is not None:
        raise ToolError(
            "name_contains only filters tag names; it cannot be combined with name. "
            "Call list_tags(name=...) without name_contains to list all values of a tag."
        )
    if name is None:
        filters = {"name:like": f"%{name_contains}%"} if name_contains else None
        params = list_params(count=count, offset=offset, sort="-usages", filters=filters)
        rows, total = _rows(await deps.client.get_json("/tags/names", params))
        names = [
            TagName(name=str(row.get("name", "")), values=_int(row, "values"), **_counts(row))
            for row in rows
        ]
        return TagList(name=None, names=names, total=total, offset=offset)

    params = {"name": name, **list_params(count=count, offset=offset, sort="-usages")}
    rows, total = _rows(await deps.client.get_json("/tags/values-for-name", params))
    values = [TagValue(value=str(row.get("value", "")), **_counts(row)) for row in rows]
    return TagList(name=name, values=values, total=total, offset=offset)
