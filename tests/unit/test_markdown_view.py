"""strip_export_title: the title BookStack puts before a markdown export."""

from bookstack_mcp.content.markdown_view import strip_export_title


def test_strips_exact_title_and_blank_line() -> None:
    assert strip_export_title("# Intro\n\nBody\n\n## Sub\n", "Intro") == "Body\n\n## Sub\n"


def test_keeps_a_second_heading_with_the_same_name() -> None:
    assert strip_export_title("# Intro\n\n# Intro\n\nx", "Intro") == "# Intro\n\nx"


def test_strips_a_title_that_differs_from_the_name() -> None:
    assert strip_export_title("# A \\*b\\*\n\nBody", "A *b*") == "Body"


def test_strips_crlf_title() -> None:
    assert strip_export_title("# T\r\n\r\nBody", "T") == "Body"


def test_title_only_export_is_empty() -> None:
    assert strip_export_title("# T\n\n", "T") == ""
    assert strip_export_title("# T", "T") == ""


def test_leaves_other_text_alone() -> None:
    assert strip_export_title("Body\n\n# T\n\n", "T") == "Body\n\n# T\n\n"
    assert strip_export_title("# T\nBody", "T") == "# T\nBody"


def test_keeps_include_tags() -> None:
    body = "{{@12#bkmrk-intro}}\n\nText"
    assert strip_export_title(f"# T\n\n{body}", "T") == body
