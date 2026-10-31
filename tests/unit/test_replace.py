import pytest
from pydantic import BaseModel

from bookstack_mcp.editing.errors import (
    AnchorAmbiguous,
    AnchorNotFound,
    InvalidArgument,
    InvalidEdit,
)
from bookstack_mcp.editing.replace import Edit, apply_edits, excerpt


def test_single_replacement_reports_context_in_new_text() -> None:
    result = apply_edits("alpha beta gamma", [Edit("beta", "BETA")])
    assert result.text == "alpha BETA gamma"
    [applied] = result.applied
    assert (applied.index, applied.occurrences_replaced) == (0, 1)
    assert applied.context == "alpha BETA gamma"


def test_chained_edit_replaces_text_an_earlier_edit_added() -> None:
    result = apply_edits("one two", [Edit("two", "deux"), Edit("deux", "zwei")])
    assert result.text == "one zwei"
    assert [a.index for a in result.applied] == [0, 1]


def test_deletion_with_empty_new_string() -> None:
    result = apply_edits("keep drop keep", [Edit(" drop", "")])
    assert result.text == "keep keep"


def test_replace_all_counts_every_occurrence() -> None:
    result = apply_edits("a x b x c x", [Edit("x", "y", replace_all=True)])
    assert result.text == "a y b y c y"
    assert result.applied[0].occurrences_replaced == 3


def test_ambiguous_anchor_lists_first_three_with_context() -> None:
    text = ("pad " * 50 + "dup ") * 5
    with pytest.raises(AnchorAmbiguous) as err:
        apply_edits(text, [Edit("dup", "one")])
    assert err.value.edit_index == 0
    assert err.value.count == 5
    assert len(err.value.matches) == 3
    for m in err.value.matches:
        assert "dup" in m
        assert m.startswith("…")
        assert len(m) <= 120 + 3 + 120 + 2


@pytest.mark.parametrize(
    ("text", "old", "offsets"),
    [("ababa", "aba", [0, 2]), ("a\n\n\nb", "\n\n", [1, 2]), ("aaaa", "aa", [0, 1, 2])],
)
def test_overlapping_occurrences_are_ambiguous(text: str, old: str, offsets: list[int]) -> None:
    with pytest.raises(AnchorAmbiguous) as err:
        apply_edits(text, [Edit(old, "X")])
    assert err.value.count == len(offsets)
    assert err.value.matches == [excerpt(text, at, at + len(old)) for at in offsets]


def test_replace_all_keeps_non_overlapping_semantics() -> None:
    result = apply_edits("ababa", [Edit("aba", "X", replace_all=True)])
    assert result.text == "Xba"
    assert result.applied[0].occurrences_replaced == 1


def test_not_found_with_whitespace_near_miss() -> None:
    text = "<p>Hello\n    world, again</p>"
    with pytest.raises(AnchorNotFound) as err:
        apply_edits(text, [Edit(" Hello world, ", "Hi")])
    assert err.value.edit_index == 0
    assert err.value.near_miss == "Hello\n    world,"
    assert "near_miss" in err.value.hint
    assert 'format="source"' in str(err.value)


def test_near_miss_is_cut_to_300_characters() -> None:
    words = " ".join(f"w{i}" for i in range(200))
    text = words.replace(" ", "\n")
    with pytest.raises(AnchorNotFound) as err:
        apply_edits(text, [Edit(words, "z")])
    assert err.value.near_miss is not None
    assert len(err.value.near_miss) == 301
    assert err.value.near_miss.endswith("…")


def test_not_found_without_near_miss() -> None:
    with pytest.raises(AnchorNotFound) as err:
        apply_edits("abc", [Edit("zzz", "y")])
    assert err.value.near_miss is None
    assert "read_page" in err.value.hint


def test_whitespace_only_anchor_has_no_near_miss() -> None:
    with pytest.raises(AnchorNotFound) as err:
        apply_edits("abc", [Edit("  ", "y")])
    assert err.value.near_miss is None


def test_later_failure_reports_its_index() -> None:
    with pytest.raises(AnchorNotFound) as err:
        apply_edits("abc", [Edit("a", "A"), Edit("a", "b")])
    assert err.value.edit_index == 1


@pytest.mark.parametrize(("old", "new"), [("", "x"), ("same", "same")])
def test_invalid_edits(old: str, new: str) -> None:
    with pytest.raises(InvalidEdit) as err:
        apply_edits("same", [Edit(old, new)])
    assert err.value.edit_index == 0
    assert isinstance(err.value, InvalidArgument)


def test_no_edits() -> None:
    with pytest.raises(InvalidArgument):
        apply_edits("abc", [])


def test_regex_characters_are_literal() -> None:
    assert apply_edits("a.b a+b", [Edit("a+b", "c")]).text == "a.b c"


def test_accepts_pydantic_models() -> None:
    class EditIn(BaseModel):
        old_string: str
        new_string: str
        replace_all: bool = False

    assert apply_edits("abc", [EditIn(old_string="b", new_string="B")]).text == "aBc"


def test_excerpt_marks_cuts() -> None:
    text = "0123456789"
    assert excerpt(text, 4, 5, radius=2) == "…23456…"
    assert excerpt(text, 0, 10, radius=2) == text
