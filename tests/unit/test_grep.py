import pytest

from bookstack_mcp.editing.errors import InvalidArgument
from bookstack_mcp.editing.grep import GrepMatch, grep


def test_case_insensitive_by_default() -> None:
    result = grep("Foo foo FOO", "foo", context=1)
    assert [m.offset for m in result.matches] == [0, 4, 8]
    assert [m.match for m in result.matches] == ["Foo", "foo", "FOO"]
    assert result.total == 3
    assert not result.truncated


def test_case_sensitive() -> None:
    result = grep("Foo foo FOO", "foo", case_sensitive=True)
    assert result.total == 1
    assert result.matches[0].offset == 4


def test_excerpt_with_ellipses() -> None:
    text = "0123456789needle0123456789"
    [m] = grep(text, "needle", context=3).matches
    assert m == GrepMatch(10, "needle", "…789needle012…")


def test_excerpt_not_cut_at_edges() -> None:
    [m] = grep("ab needle cd", "needle", context=50).matches
    assert m.excerpt == "ab needle cd"


def test_total_counts_beyond_max_matches() -> None:
    result = grep("x " * 30, "x", max_matches=5)
    assert len(result.matches) == 5
    assert result.total == 30
    assert result.truncated


def test_regex_characters_are_literal() -> None:
    text = "a.b axb (a+b)* [x] a+b $1"
    assert grep(text, "a.b").total == 1
    assert grep(text, "(a+b)*").matches[0].offset == text.index("(a+b)*")
    assert grep(text, "[x]").total == 1
    assert grep(text, "$1").total == 1
    assert grep(text, ".*").total == 0


def test_no_match() -> None:
    result = grep("abc", "z")
    assert result.matches == []
    assert result.total == 0
    assert not result.truncated


def test_matches_do_not_overlap() -> None:
    assert grep("aaaa", "aa").total == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query": ""}, "empty"),
        ({"query": "a", "context": 0}, "context"),
        ({"query": "a", "context": 2001}, "context"),
        ({"query": "a", "max_matches": 0}, "max_matches"),
        ({"query": "a", "max_matches": 51}, "max_matches"),
    ],
)
def test_invalid_arguments(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidArgument, match=message):
        grep("abc", **kwargs)  # type: ignore[arg-type]


def test_bounds_are_inclusive() -> None:
    assert grep("a", "a", context=1, max_matches=1).total == 1
    assert grep("a", "a", context=2000, max_matches=50).total == 1
