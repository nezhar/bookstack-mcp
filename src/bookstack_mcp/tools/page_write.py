"""The shared write path for tools that change a page's content.

`write_page` reads the page, lets the caller compute the new source from it, runs the
guards, writes only the source field and verifies what BookStack stored. `edit_page`,
`append_to_page` and `update_page` use it; this module declares no tools.

    def compute(source: PageSource) -> PageChange:
        text = insert(source.text, content, source.field)
        return PageChange(text, checks_for_insert(content, source.field))

    result = await write_page(deps, page_id, compute, changelog="Appended via bookstack-mcp")
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from bookstack_mcp.editing import diff
from bookstack_mcp.editing.errors import PageStale
from bookstack_mcp.editing.guards import check_shrink, check_stale
from bookstack_mcp.editing.source import PageSource, SourceField, select_source
from bookstack_mcp.editing.verify import Checks, verify
from bookstack_mcp.tools._base import Deps

__all__ = [
    "ChangeContext",
    "Compute",
    "PageChange",
    "PageWriteResult",
    "write_page",
]


class ChangeContext(BaseModel):
    """Where one change landed, as an excerpt of the new source around it."""

    index: int = Field(description="The change's position in the request, from 0.")
    occurrences_replaced: int = Field(
        default=1, description="How many occurrences the change replaced."
    )
    context: str = Field(description="The new source around the (first) change.")


@dataclass(frozen=True, slots=True)
class PageChange:
    """What a tool wants written, computed from the page's current source.

    - `text`: the complete new source.
    - `checks`: what must (not) be in the stored source afterwards (`editing.verify`).
    - `contexts`: where each change landed, returned to the model.
    - `field`: write the text as this field instead of the page's source field. Only for a
      deliberate format change (`update_page` with `allow_format_change`).
    """

    text: str
    checks: Checks
    contexts: Sequence[ChangeContext] = ()
    field: SourceField | None = None


Compute = Callable[[PageSource], PageChange]
"""Builds the change from the current source. May raise `EditingError` or `ToolError`."""


class PageWriteResult(BaseModel):
    """The outcome of a page write. Never contains the page content."""

    page_id: int
    name: str
    url: str
    source_field: SourceField = Field(description="The field written: markdown or html.")
    chars_before: int
    chars_after: int
    delta: int
    contexts: list[ChangeContext] = Field(description="Where each change landed.")
    diff: str = Field(description="Unified diff of the changed lines, cut when long.")
    dry_run: bool
    written: bool
    verified: bool | None = Field(
        description="Whether the re-read page holds the change; null when nothing was written."
    )
    unverified_count: int = Field(description="How many verification checks failed.")
    updated_at: str | None = Field(description="The page's updated_at after the write.")
    revision_count: int | None = Field(description="The page's revision count after the write.")
    notes: list[str] = Field(default_factory=list)


async def write_page(
    deps: Deps,
    page_id: int,
    compute: Compute,
    *,
    changelog: str,
    dry_run: bool = False,
    expected_updated_at: str | None = None,
    allow_shrink: bool = False,
    extra_body: Mapping[str, Any] | None = None,
    rebuild: str = "change",
) -> PageWriteResult:
    """Read the page, apply `compute`, guard, write the source field and verify.

    1. `GET /pages/{id}`; refuse a stale page when `expected_updated_at` is given.
    2. `select_source`, `compute`, then the shrink guard unless `allow_shrink`.
    3. With `dry_run`, return the sizes and the diff without writing.
    4. `PUT /pages/{id}` with only the source field (`markdown`, or `html` for `raw_html`
       sources), `changelog` on BookStack 26.09+, and `extra_body` (such as `name` or `tags`
       for `update_page`).
    5. Re-read the page and verify the stored source against the change's checks.

    `rebuild` names what the caller must redo after a stale refusal ("anchors" for
    edit_page). Errors from `compute` and the guards propagate for `tool_call` to map.
    """
    page = await deps.client.get_json(f"/pages/{page_id}")
    try:
        check_stale(str(page.get("updated_at")), expected_updated_at)
    except PageStale as exc:
        raise ToolError(
            f"Page {page_id} changed since it was read: its updated_at is "
            f"{exc.page_updated_at}, but expected_updated_at was {exc.expected_updated_at}. "
            f"Read the page again and rebuild the {rebuild}."
        ) from exc

    source = select_source(page)
    change = compute(source)
    write_field = change.field or source.field
    check_shrink(source.text, change.text, allow_shrink)

    notes: list[str] = []
    result = PageWriteResult(
        page_id=page_id,
        name=str(page.get("name", "")),
        url=deps.links.page(page_id),
        source_field=write_field,
        chars_before=len(source.text),
        chars_after=len(change.text),
        delta=len(change.text) - len(source.text),
        contexts=list(change.contexts),
        diff=diff.summary(source.text, change.text),
        dry_run=dry_run,
        written=False,
        verified=None,
        unverified_count=0,
        updated_at=page.get("updated_at"),
        revision_count=page.get("revision_count"),
    )
    if dry_run:
        return result.model_copy(update={"notes": ["Dry run: nothing was written."]})

    body: dict[str, Any] = dict(extra_body or {})
    body[write_field] = change.text
    if deps.features.changelog:
        body["changelog"] = changelog
    else:
        notes.append(
            f"changelog not sent: BookStack {deps.system.version} does not accept it "
            "(needs 26.09 or later)."
        )
    await deps.client.put_json(f"/pages/{page_id}", body)

    stored_page = await deps.client.get_json(f"/pages/{page_id}")
    stored = select_source(stored_page)
    checks = change.checks
    if stored.field != write_field:
        verified, failures = False, max(1, len(checks.must_contain) + len(checks.must_not_contain))
        notes.append(
            f"The page was written as {write_field} but BookStack stored it as {stored.field}."
        )
    else:
        verification = verify(stored.text, stored.field, checks)
        verified, failures = verification.verified, verification.unverified_count
    if not verified:
        notes.append(
            f"{failures} verification check(s) failed: the page was written, but the stored "
            "source does not show every change. Read the page to see what BookStack stored."
        )
    return result.model_copy(
        update={
            "name": str(stored_page.get("name", result.name)),
            "written": True,
            "verified": verified,
            "unverified_count": failures,
            "updated_at": stored_page.get("updated_at"),
            "revision_count": stored_page.get("revision_count"),
            "notes": notes,
        }
    )
