import pytest

from bookstack_mcp.client import Features, Links, SystemInfo, parse_version


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v26.09.2", (26, 9, 2)),
        ("26.05.5", (26, 5, 5)),
        ("v25.11", (25, 11, 0)),
        ("v26.09.2-dev", (26, 9, 2)),
    ],
)
def test_parse_version(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "dev", "vX.1", "26"])
def test_parse_version_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError, match="not a BookStack version"):
        parse_version(text)


def test_features_26_09() -> None:
    assert Features.from_version("v26.09.0") == Features(
        changelog=True,
        scoped_search=True,
        tags_api=True,
        comments_api=True,
        image_data=True,
        imports_api=True,
    )


def test_features_26_05() -> None:
    f = Features.from_version("v26.05.5")
    assert (f.changelog, f.scoped_search) == (False, False)
    assert (f.tags_api, f.comments_api, f.image_data, f.imports_api) == (True, True, True, True)


def test_features_25_11() -> None:
    f = Features.from_version((25, 11, 0))
    assert not f.tags_api
    assert f.comments_api and f.image_data and f.imports_api


def test_features_25_07() -> None:
    f = Features.from_version("v25.07.3")
    assert not f.comments_api and not f.image_data
    assert f.imports_api


def test_features_25_02() -> None:
    f = Features.from_version("v25.02")
    assert f == Features(False, False, False, False, False, False)


def test_links() -> None:
    links = Links("https://docs.example.com/")
    assert links.page(12) == "https://docs.example.com/link/12"
    assert links.book("guide") == "https://docs.example.com/books/guide"
    assert links.chapter("guide", "intro") == "https://docs.example.com/books/guide/chapter/intro"
    assert links.shelf("team") == "https://docs.example.com/shelves/team"


def test_links_prefer_item_url() -> None:
    assert (
        Links.prefer({"url": "https://x/books/a/page/b"}, "fallback") == "https://x/books/a/page/b"
    )
    assert Links.prefer({"url": ""}, "fallback") == "fallback"
    assert Links.prefer({}, "fallback") == "fallback"


def test_system_info_helpers() -> None:
    info = SystemInfo.model_validate(
        {
            "version": "v26.05.5",
            "instance_id": "abc",
            "app_name": "BookStack",
            "app_logo": None,
            "base_url": "https://docs.example.com",
            "extra": 1,
        }
    )
    assert info.version_tuple == (26, 5, 5)
    assert info.features.tags_api
    assert info.links.page(3) == "https://docs.example.com/link/3"
