"""parse_since: ISO dates and date-times to UTC."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastmcp.exceptions import ToolError

from bookstack_mcp.tools.recent_changes import parse_since

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-01", datetime(2026, 10, 1, tzinfo=UTC)),
        ("2026-10-01T14:30:00", datetime(2026, 10, 1, 14, 30, tzinfo=UTC)),
        ("2026-10-01T14:30:00Z", datetime(2026, 10, 1, 14, 30, tzinfo=UTC)),
        ("2026-10-01 14:30:00+02:00", datetime(2026, 10, 1, 12, 30, tzinfo=UTC)),
        (" 2026-10-01T00:30-01:00 ", datetime(2026, 10, 1, 1, 30, tzinfo=UTC)),
    ],
)
def test_parses_iso(text: str, expected: datetime) -> None:
    assert parse_since(text, NOW) == expected


def test_default_is_seven_days_ago() -> None:
    assert parse_since(None, NOW) == datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("text", ["", "yesterday", "2026-13-01", "01/10/2026"])
def test_rejects_other_text(text: str) -> None:
    with pytest.raises(ToolError, match="ISO"):
        parse_since(text, NOW)
