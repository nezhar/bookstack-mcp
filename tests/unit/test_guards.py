import pytest

from bookstack_mcp.editing.errors import PageStale, ShrinkRefused
from bookstack_mcp.editing.guards import check_shrink, check_stale


def test_shrink_below_half_is_refused_with_sizes() -> None:
    with pytest.raises(ShrinkRefused) as err:
        check_shrink("x" * 100, "x" * 49, allow_shrink=False)
    assert (err.value.chars_before, err.value.chars_after) == (100, 49)
    assert "allow_shrink" in str(err.value)


def test_shrink_to_exactly_half_or_allowed_passes() -> None:
    check_shrink("x" * 100, "x" * 50, allow_shrink=False)
    check_shrink("x" * 100, "", allow_shrink=True)
    check_shrink("", "", allow_shrink=False)


def test_stale_compares_strings_exactly() -> None:
    check_stale("2026-10-30T10:00:00.000000Z", "2026-10-30T10:00:00.000000Z")
    check_stale("2026-10-30T10:00:00.000000Z", None)
    with pytest.raises(PageStale) as err:
        # Same instant, different spelling: still a mismatch.
        check_stale("2026-10-30T10:00:00.000000Z", "2026-10-30T10:00:00Z")
    assert err.value.page_updated_at == "2026-10-30T10:00:00.000000Z"
    assert err.value.expected_updated_at == "2026-10-30T10:00:00Z"
