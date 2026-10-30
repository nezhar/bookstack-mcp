"""The `recent_changes` tool: pages updated since a point in time, newest first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.client.http import ParamValue
from bookstack_mcp.config import Toolset
from bookstack_mcp.tools import CurrentDeps, Deps, tool

DEFAULT_DAYS = 7


class ChangedPage(BaseModel):
    """A page and when, and by whom, it was last updated."""

    page_id: int
    name: str
    url: str
    book_id: int
    chapter_id: int | None = Field(description="None when the page is not in a chapter.")
    updated_at: str
    updated_by: int | None = Field(description="The id of the user who last updated it.")


class RecentChanges(BaseModel):
    """Pages updated at or after `since`, newest first."""

    since: str = Field(description="The cut-off used, in UTC.")
    pages: list[ChangedPage]
    total: int = Field(description="Matching pages across all pages of results.")
    offset: int


def parse_since(since: str | None, now: datetime | None = None) -> datetime:
    """`since` as an aware UTC datetime. Dates mean midnight UTC; naive times are UTC.

    Without `since`, seven days before `now`.
    """
    if since is None:
        return (now or datetime.now(UTC)) - timedelta(days=DEFAULT_DAYS)
    try:
        moment = datetime.fromisoformat(since.strip())
    except ValueError:
        raise ToolError(
            f"since {since!r} is not an ISO date or date-time; use e.g. 2026-10-01 "
            "or 2026-10-01T14:30:00Z."
        ) from None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _optional_id(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


@tool(Toolset.CORE)
async def recent_changes(
    since: Annotated[
        str | None,
        Field(
            description=(
                "ISO date or date-time (e.g. 2026-10-01 or 2026-10-01T14:30:00Z); "
                "times without a zone are UTC. Default: 7 days ago."
            ),
            max_length=64,
        ),
    ] = None,
    book_id: Annotated[int | None, Field(description="Only pages in this book.", ge=1)] = None,
    count: Annotated[int, Field(description="Pages per call.", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Pages to skip, for paging.", ge=0)] = 0,
    deps: Deps = CurrentDeps(),
) -> RecentChanges:
    """List pages updated since a point in time, newest first.

    Each page comes with its book and chapter ids, when it was last updated and the id of
    the user who did it. Drafts are left out. Use `book_id` to look at one book.
    """
    cutoff = parse_since(since)
    filters: dict[str, ParamValue] = {
        "updated_at:gte": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "draft": False,
        "book_id": book_id,
    }
    result = await deps.client.list_page(
        "/pages", count=count, offset=offset, sort="-updated_at", filters=filters
    )
    pages = [
        ChangedPage(
            page_id=row["id"],
            name=str(row.get("name", "")),
            url=deps.links.page(row["id"]),
            book_id=row.get("book_id") if isinstance(row.get("book_id"), int) else 0,
            chapter_id=_optional_id(row.get("chapter_id")),
            updated_at=str(row.get("updated_at", "")),
            updated_by=_optional_id(row.get("updated_by")),
        )
        for row in result.data
        if isinstance(row.get("id"), int)
    ]
    return RecentChanges(
        since=cutoff.isoformat().replace("+00:00", "Z"),
        pages=pages,
        total=result.total,
        offset=offset,
    )
