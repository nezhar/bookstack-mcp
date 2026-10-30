from bookstack_mcp.editing.diff import MAX_CHARS, MAX_LINES, summary


def test_changed_lines_only() -> None:
    before = "a\nb\nc\nd\n"
    after = "a\nB\nc\nd\ne\n"
    out = summary(before, after)
    assert out.splitlines() == ["@@ -2 +2 @@", "-b", "+B", "@@ -4,0 +5 @@", "+e"]


def test_no_change_is_empty() -> None:
    assert summary("same\n", "same\n") == ""


def test_cut_at_line_limit() -> None:
    before = "\n".join(str(i) for i in range(100))
    after = "\n".join(f"{i}!" for i in range(100))
    out = summary(before, after)
    lines = out.splitlines()
    assert len(lines) == MAX_LINES + 1
    assert lines[-1].startswith("… diff cut")


def test_cut_at_character_limit() -> None:
    before = "x" * 3000 + "\n" + "y" * 3000
    after = "X" * 3000 + "\n" + "Y" * 3000
    out = summary(before, after)
    body, note = out.rsplit("\n", 1)
    assert len(body) == MAX_CHARS
    assert note.startswith("… diff cut")


def test_single_long_line_is_cut() -> None:
    out = summary("", "z" * 10000)
    body, note = out.rsplit("\n", 1)
    assert len(body) == MAX_CHARS
    assert note.startswith("… diff cut")


def test_line_exactly_filling_the_budget_then_more() -> None:
    header = "@@ -0,0 +1,2 @@"
    first = "a" * (MAX_CHARS - len(header) - 2)  # header, newline and "+" + first fill it
    out = summary("", first + "\nb")
    body, note = out.rsplit("\n", 1)
    assert body == header + "\n+" + first
    assert note.startswith("… diff cut")
