import pytest

from bookstack_mcp.editing.append import insert, plain_text_to_html, prepare_content
from bookstack_mcp.editing.errors import InvalidArgument, SectionNotFound

MD = """\
# Title

intro

## Setup

setup text

### Details

details

## Usage

usage
"""


def test_end_of_markdown_page_steps_back_over_trailing_whitespace() -> None:
    assert insert(MD, "new", "markdown").endswith("usage\n\nnew\n")


def test_end_of_empty_page() -> None:
    assert insert("", "new", "markdown") == "new"
    assert insert("\n\n", "new", "html") == "<p>new</p>\n\n"


def test_section_end_includes_subsections() -> None:
    out = insert(MD, "added", "markdown", position="section_end", section="Setup")
    assert "details\n\nadded\n\n## Usage" in out


def test_section_end_of_last_section() -> None:
    out = insert(MD, "added", "markdown", position="section_end", section="usage")
    assert out.endswith("usage\n\nadded\n")


def test_section_end_of_empty_section_stays_after_heading() -> None:
    text = "## A\n\n## B\nb\n"
    out = insert(text, "x", "markdown", position="section_end", section="A")
    assert out == "## A\n\nx\n\n## B\nb\n"


def test_after_heading_markdown() -> None:
    out = insert(MD, "first", "markdown", position="after_heading", section="Setup")
    assert "## Setup\n\nfirst\n\nsetup text" in out


def test_after_heading_on_last_line_and_crlf() -> None:
    assert insert("# T", "x", "markdown", position="after_heading", section="T") == "# T\n\nx"
    out = insert("# T\r\nbody", "x", "markdown", position="after_heading", section="T")
    assert out == "# T\n\nx\n\nbody"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# H\nPara\n", "# H\n\nNew text\n\nPara\n"),
        ("# H\n## Sub\n", "# H\n\nNew text\n\n## Sub\n"),
        ("# H\n\nPara\n", "# H\n\nNew text\n\nPara\n"),
        ("# H  \n  \nPara", "# H  \n\nNew text\n  \nPara"),
        ("# H\n\n", "# H\n\nNew text\n\n"),
    ],
)
def test_after_heading_markdown_keeps_the_next_block_apart(text: str, expected: str) -> None:
    out = insert(text, "New text", "markdown", position="after_heading", section="H")
    assert out == expected


def test_after_heading_html_without_line_break() -> None:
    out = insert("<h2>H</h2><p>P</p>", "<p>New</p>", "html", position="after_heading", section="H")
    assert out == "<h2>H</h2>\n<p>New</p>\n<p>P</p>"


HTML = '<h2 id="a">Setup</h2>\n<p>one</p>\n<h3>Sub</h3><p>two</p>\n<h2>Usage</h2><p>u</p>'


def test_after_heading_html_goes_after_closing_tag() -> None:
    out = insert(HTML, "<p>x</p>", "html", position="after_heading", section="Setup")
    assert out.startswith('<h2 id="a">Setup</h2>\n<p>x</p>\n<p>one</p>')


def test_section_end_html() -> None:
    out = insert(HTML, "<p>x</p>", "html", position="section_end", section="Setup")
    assert "<p>two</p>\n<p>x</p>\n<h2>Usage</h2>" in out


def test_plain_text_is_wrapped_for_html_page() -> None:
    out = insert("<p>a</p>", "Tom & Jerry <3\n\n  second\n  line  \n\n\n", "html")
    assert out == "<p>a</p>\n<p>Tom &amp; Jerry &lt;3</p>\n<p>second\n  line</p>"


def test_html_content_is_inserted_as_is() -> None:
    assert insert("<p>a</p>", "<ul><li>x</li></ul>", "html") == "<p>a</p>\n<ul><li>x</li></ul>"


def test_markdown_content_is_not_wrapped() -> None:
    assert prepare_content("a < b", "markdown") == "a < b"
    assert plain_text_to_html("a\r\n\r\nb") == "<p>a</p>\n<p>b</p>"


def test_custom_separator() -> None:
    assert insert("a", "b", "markdown", separator="\n---\n") == "a\n---\nb"


def test_unknown_section() -> None:
    with pytest.raises(SectionNotFound):
        insert(MD, "x", "markdown", position="section_end", section="Nope")


@pytest.mark.parametrize(
    ("content", "position", "section"),
    [
        ("  ", "end", None),
        ("x", "end", "Setup"),
        ("x", "section_end", None),
        ("x", "after_heading", ""),
        ("x", "middle", "Setup"),
    ],
)
def test_invalid_arguments(content: str, position: str, section: str | None) -> None:
    with pytest.raises(InvalidArgument):
        insert(MD, content, "markdown", position=position, section=section)  # type: ignore[arg-type]
