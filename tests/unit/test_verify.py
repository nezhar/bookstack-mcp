import pytest

from bookstack_mcp.editing.append import insert
from bookstack_mcp.editing.replace import Edit, apply_edits
from bookstack_mcp.editing.verify import (
    Checks,
    Removed,
    checks_for_edits,
    checks_for_insert,
    contains,
    normalise,
    verify,
)


def test_normalise() -> None:
    assert normalise("<p>a&amp;b&nbsp; <b>c</b></p>\n", "html") == "a&b c"
    assert normalise("  # T\n\n  x  ", "markdown") == "# T x"


def test_verification_after_bookstack_rewrites_html() -> None:
    before = "<h2>Old title</h2>\n<p>Body text</p>"
    edits = [Edit("<h2>Old title</h2>", "<h2>New title</h2>")]
    after = apply_edits(before, edits).text
    stored = '<h2 id="bkmrk-new-title">New title</h2>\n<p id="bkmrk-body-text">Body   text</p>'
    result = verify(stored, "html", checks_for_edits(before, after, edits, "html"))
    assert result.verified
    assert result.unverified_count == 0


def test_missing_fragment_is_counted_not_raised() -> None:
    checks = Checks(["<p>gone</p>"], [])
    result = verify("<p>other</p>", "html", checks)
    assert not result.verified
    assert result.unverified_count == 1


def test_chained_edit_only_requires_the_final_fragment() -> None:
    before = "one two"
    edits = [Edit("two", "deux"), Edit("deux", "zwei")]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "markdown")
    assert checks.must_contain == ["zwei"]
    # Edit 0 was overwritten, so its old anchor's absence is the evidence.
    assert checks.must_not_contain == [Removed("two")]
    assert verify(after, "markdown", checks).verified
    assert not verify(before, "markdown", checks).verified


def test_deletion_requires_old_string_gone() -> None:
    before = "keep drop keep"
    edits = [Edit(" drop", "")]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "markdown")
    assert checks == Checks([], [Removed(" drop")])
    assert verify(after, "markdown", checks).verified
    assert verify(before, "markdown", checks).unverified_count == 1


def test_replacement_already_in_text_requires_old_string_gone() -> None:
    before = "fixed bug fixed"
    edits = [Edit("bug", "fixed")]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "markdown")
    assert checks == Checks(["fixed"], [Removed("bug")])


def test_new_text_proves_edit_so_old_string_not_checked() -> None:
    before = "a old b"
    edits = [Edit("old", "new")]
    after = apply_edits(before, edits).text
    assert checks_for_edits(before, after, edits, "markdown") == Checks(["new"], [])


def test_restored_anchor_is_not_required_gone() -> None:
    before = "x A y"
    edits = [Edit("A", "B"), Edit("B", "A")]
    after = apply_edits(before, edits).text
    # Edit 0's anchor was restored by edit 1; edit 1's "A" existed before, so "B" must be gone.
    assert checks_for_edits(before, after, edits, "markdown") == Checks(["A"], [Removed("B")])


def test_markup_only_fragment_is_checked_on_raw_text() -> None:
    assert contains("<p>a</p>\n<hr>\n<p>b</p>", "<hr>", "html")
    assert not contains("<p>a</p><p>b</p>", "<hr>", "html")
    assert contains("<p>a</p>  <hr>", "</p>\n<hr>", "html")
    # Markup-only replacement already present before: the old anchor must be gone.
    before = "<p>a</p><hr><p>b</p>"
    edits = [Edit("<p>b</p>", "<hr>")]
    after = apply_edits(before, edits).text
    assert checks_for_edits(before, after, edits, "html") == Checks(["<hr>"], [Removed("<p>b</p>")])


def test_insert_checks_use_the_wrapped_plain_text() -> None:
    content = "x<y and z>w & co"
    written = insert("<p>a</p>", content, "html")
    stored = written.replace("<p>", '<p id="bkmrk-1">')
    assert verify(stored, "html", checks_for_insert(content, "html")).verified
    assert checks_for_insert("**b**", "markdown") == Checks(["**b**"], [])


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('<a href="http://old">Link</a>', '<a href="http://new">Link</a>'),
        ("<b>word</b>", "<em>word</em>"),
        ("<p>word</p>", "<h2>word</h2>"),
    ],
)
def test_markup_only_html_edit_is_checked_on_raw_text(old: str, new: str) -> None:
    before = f"<p>See</p>\n{old}\n<p>word Link</p>"
    edits = [Edit(old, new)]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "html")
    assert checks.must_not_contain == [Removed(old, 0, raw=True)]
    stored = after.replace("<p>", '<p id="bkmrk-1">')
    assert verify(stored, "html", checks).verified
    # A write that did not stick still holds the old markup.
    assert verify(before, "html", checks).unverified_count == 1


def test_removed_text_that_occurs_elsewhere_is_counted() -> None:
    before = "Tip  one\n\nTip one\n"
    edits = [Edit("Tip one\n", "")]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "markdown")
    assert checks.must_not_contain == [Removed("Tip one\n", 1)]
    assert verify(after, "markdown", checks).verified
    assert not verify(before, "markdown", checks).verified


def test_deleted_html_whose_words_occur_elsewhere_is_counted() -> None:
    before = "<p>We use Docker.</p><ul><li>Docker</li><li>uv</li></ul>"
    edits = [Edit("<li>Docker</li>", "")]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "html")
    assert checks.must_not_contain == [Removed("<li>Docker</li>", 1)]
    assert verify(after.replace("<p>", '<p id="bkmrk-x">'), "html", checks).verified
    assert not verify(before, "html", checks).verified


def test_whitespace_only_deletion_is_counted_exactly() -> None:
    before = "a\n\n\nb"
    edits = [Edit("\n\n\n", "\n\n")]
    after = apply_edits(before, edits).text
    checks = checks_for_edits(before, after, edits, "markdown")
    assert checks.must_not_contain == [Removed("\n\n\n", 0, raw=True)]
    assert verify(after, "markdown", checks).verified
    assert not verify(before, "markdown", checks).verified


def test_unchanged_count_without_proof_adds_no_check() -> None:
    # "A" -> "B" -> "A": the page ends as it started, so nothing distinguishes the write.
    before = "x A y"
    edits = [Edit("A", "B"), Edit("B", "A")]
    checks = checks_for_edits(before, apply_edits(before, edits).text, edits, "markdown")
    assert Removed("A") not in checks.must_not_contain
