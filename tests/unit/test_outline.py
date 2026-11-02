import pytest

from bookstack_mcp.editing.errors import SectionAmbiguous, SectionNotFound
from bookstack_mcp.editing.outline import Heading, build_outline, find_section

MD = """\
# Title

intro
## Setup
setup text
### Details
details
## Usage
usage
# Appendix
end
"""


def test_markdown_levels_offsets_and_lengths() -> None:
    outline = build_outline(MD, "markdown")
    assert [(h.level, h.text) for h in outline] == [
        (1, "Title"),
        (2, "Setup"),
        (3, "Details"),
        (2, "Usage"),
        (1, "Appendix"),
    ]
    for h in outline:
        assert MD[h.offset :].startswith("#" * h.level + " " + h.text)
        assert h.anchor is None
    title, setup, details, usage, appendix = outline
    assert title.length == setup.offset - title.offset
    assert title.section_length == appendix.offset - title.offset
    # section_length spans the subsection, length stops at it
    assert setup.length == details.offset - setup.offset
    assert setup.section_length == usage.offset - setup.offset
    assert details.section_length == details.length == usage.offset - details.offset
    assert usage.section_length == appendix.offset - usage.offset
    assert appendix.length == appendix.section_length == len(MD) - appendix.offset
    assert MD[setup.offset : setup.offset + setup.section_length].endswith("details\n")


def test_markdown_headings_inside_fences_are_ignored() -> None:
    text = (
        "# Real\n"
        "```bash\n"
        "# a shell comment\n"
        "~~~\n"
        "## still code\n"
        "```\n"
        "~~~~\n"
        "# tilde code\n"
        "```\n"
        "~~~\n"  # too short to close a four-tilde fence
        "~~~~~\n"
        "## After\n"
    )
    assert [h.text for h in build_outline(text, "markdown")] == ["Real", "After"]


def test_unclosed_fence_hides_the_rest() -> None:
    assert [h.text for h in build_outline("# A\n```\n# B\n", "markdown")] == ["A"]


def test_fence_with_trailing_text_does_not_close() -> None:
    text = "```\n``` not a close\n# code\n```\n# Out\n"
    assert [h.text for h in build_outline(text, "markdown")] == ["Out"]


def test_markdown_heading_forms() -> None:
    text = "#NoSpace\n####### seven\n#\tTabbed  \n## Closed ##\n# C#\n# A &amp; <b>B</b>\r\n"
    outline = build_outline(text, "markdown")
    assert [(h.level, h.text) for h in outline] == [
        (1, "Tabbed"),
        (2, "Closed"),
        (1, "C#"),
        (1, "A & B"),
    ]


def test_only_newlines_end_markdown_lines() -> None:
    text = "intro\u2028# Fake\nform\x0c# Feed\n# Real\r\nbody\n## Last"
    outline = build_outline(text, "markdown")
    assert [(h.text, h.offset) for h in outline] == [("Real", 25), ("Last", 38)]
    assert text[25:31] == "# Real"
    assert text[38:] == "## Last"


def test_html_headings_with_nested_tags_entities_and_ids() -> None:
    html = (
        '<h1 id="bkmrk-intro">Intro &amp; <em>Overview</em></h1><p>a</p>'
        "<H2 class='x'>Tom&#39;s&nbsp;&lt;tips&gt;\n  and &quot;tricks&quot;</H2><p>b</p>"
        "<h3 data-id=\"no\" id='bkmrk-deep'>Deep &#039;one&#039;</h3><p>c</p>"
        "<h2 id=bare>Next</h2><p>d</p>"
        '<h2 data-id="nope">No id</h2>'
    )
    outline = build_outline(html, "html")
    assert [(h.level, h.text, h.anchor) for h in outline] == [
        (1, "Intro & Overview", "bkmrk-intro"),
        (2, 'Tom\'s <tips> and "tricks"', None),
        (3, "Deep 'one'", "bkmrk-deep"),
        (2, "Next", "bare"),
        (2, "No id", None),
    ]
    for h in outline:
        assert html[h.offset : h.offset + 2].lower() == "<h"
    intro, tom, deep, nxt, _ = outline
    assert intro.section_length == len(html) - intro.offset
    assert tom.length == deep.offset - tom.offset
    assert tom.section_length == nxt.offset - tom.offset


def test_entities_are_not_double_decoded() -> None:
    [h] = build_outline("<h1>&amp;lt;b&amp;gt;</h1>", "html")
    assert h.text == "&lt;b&gt;"


def test_mismatched_html_heading_tags_are_skipped() -> None:
    assert build_outline("<h1>bad</h2><p>x</p>", "html") == []


def test_empty_text() -> None:
    assert build_outline("", "markdown") == []
    assert build_outline("", "html") == []


def _outline() -> list[Heading]:
    return build_outline(
        "# Install\n## Install on Linux\n## Install on macOS\n# Usage\n# Usage notes\n",
        "markdown",
    )


def test_find_section_exact_case_insensitive_beats_substring() -> None:
    assert find_section(_outline(), "install").text == "Install"
    assert find_section(_outline(), "  USAGE ").text == "Usage"


def test_find_section_unique_substring() -> None:
    assert find_section(_outline(), "linux").text == "Install on Linux"


def test_find_section_ambiguous() -> None:
    with pytest.raises(SectionAmbiguous) as err:
        find_section(_outline(), "install on")
    assert err.value.candidates == ["Install on Linux", "Install on macOS"]
    assert "Install on macOS" in str(err.value)


def test_find_section_not_found_lists_headings() -> None:
    with pytest.raises(SectionNotFound) as err:
        find_section(_outline(), "deploy")
    assert err.value.available == [
        "Install",
        "Install on Linux",
        "Install on macOS",
        "Usage",
        "Usage notes",
    ]
    assert "'Usage notes'" in str(err.value)


def test_find_section_on_page_without_headings() -> None:
    with pytest.raises(SectionNotFound, match="no headings"):
        find_section([], "x")


def test_indented_atx_heading_is_found() -> None:
    text = "# First\n\n   ## Second\n\n    # code, not a heading\n"
    assert [(h.level, h.text) for h in build_outline(text, "markdown")] == [
        (1, "First"),
        (2, "Second"),
    ]


def test_setext_headings_are_found_with_their_offsets() -> None:
    text = "Intro\n\nFirst\n=====\n\nbody\n\nSecond\n---\n\nmore\n\n---\n\n- item\n---\n"
    outline = build_outline(text, "markdown")
    assert [(h.level, h.text) for h in outline] == [(1, "First"), (2, "Second")]
    assert text[outline[0].offset :].startswith("First\n=====")
    assert text[outline[1].offset :].startswith("Second\n---")
    assert outline[0].section_length == len(text) - outline[0].offset
    assert find_section(outline, "second") is outline[1]


def test_setext_underline_inside_code_fence_is_ignored() -> None:
    text = "```\nTitle\n=====\n```\n"
    assert build_outline(text, "markdown") == []


def test_code_span_in_heading_keeps_its_content() -> None:
    outline = build_outline("# Using `List<T>` and `&amp;`\n\n## a<b>c</b>d\n", "markdown")
    assert [h.text for h in outline] == ["Using `List<T>` and `&amp;`", "acd"]
    assert find_section(outline, "List<T>") is outline[0]
