import pytest

from bookstack_mcp.editing.source import PageSource, select_source


def test_markdown_wins() -> None:
    page = {"markdown": "# Hi", "raw_html": "<h1>Hi</h1>", "html": "x", "editor": "markdown"}
    assert select_source(page) == PageSource("markdown", "# Hi", "markdown")


def test_markdown_wins_even_when_editor_says_wysiwyg() -> None:
    page = {"markdown": "# Hi", "raw_html": "<h1>Hi</h1>", "editor": "wysiwyg"}
    assert select_source(page) == PageSource("markdown", "# Hi", "wysiwyg")


def test_empty_markdown_page_stays_markdown() -> None:
    page = {"markdown": "", "raw_html": "", "html": "", "editor": "markdown"}
    assert select_source(page) == PageSource("markdown", "", "markdown")


@pytest.mark.parametrize("editor", ["wysiwyg", "wysiwyg2024", "", None])
def test_empty_non_markdown_page_is_html(editor: str | None) -> None:
    page = {"markdown": "", "raw_html": "", "editor": editor}
    assert select_source(page) == PageSource("html", "", editor or "")


def test_html_page_uses_raw_html_never_rendered_html() -> None:
    page = {
        "markdown": "",
        "raw_html": "<p>{{@12#bkmrk-x}}</p>",
        "html": "<p>included text</p>",
        "editor": "wysiwyg2024",
    }
    assert select_source(page) == PageSource("html", "<p>{{@12#bkmrk-x}}</p>", "wysiwyg2024")


def test_markdown_editor_with_html_but_no_markdown_is_html() -> None:
    page = {"markdown": "", "raw_html": "<p>a</p>", "editor": "markdown"}
    assert select_source(page).field == "html"


def test_missing_and_null_fields() -> None:
    assert select_source({}) == PageSource("html", "", "")
    assert select_source({"markdown": None, "raw_html": None}) == PageSource("html", "", "")
