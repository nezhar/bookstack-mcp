"""Named errors raised by the pure editing functions."""

from collections.abc import Sequence


class EditingError(Exception):
    """Base class for errors raised by ``bookstack_mcp.editing``."""


class InvalidArgument(EditingError, ValueError):
    """An argument is out of range or empty."""


class SectionNotFound(EditingError, LookupError):
    """No heading matches the requested section name."""

    def __init__(self, name: str, available: Sequence[str]) -> None:
        self.name = name
        self.available = list(available)
        listing = ", ".join(repr(h) for h in self.available) or "none (the page has no headings)"
        super().__init__(f"No section matches {name!r}. Available headings: {listing}")


class SectionAmbiguous(EditingError, LookupError):
    """Several headings contain the requested section name and none matches exactly."""

    def __init__(self, name: str, candidates: Sequence[str]) -> None:
        self.name = name
        self.candidates = list(candidates)
        listing = ", ".join(repr(h) for h in self.candidates)
        super().__init__(
            f"Section name {name!r} matches several headings: {listing}. Use the full heading text."
        )


ANCHOR_HINT = (
    'Copy anchors exactly from read_page, using grep or format="source", '
    "rather than from the rendered page."
)


class InvalidEdit(InvalidArgument):
    """An edit's ``old_string`` is empty or equal to its ``new_string``."""

    def __init__(self, edit_index: int, reason: str) -> None:
        self.edit_index = edit_index
        self.reason = reason
        super().__init__(f"Edit {edit_index}: {reason}")


class AnchorNotFound(EditingError, LookupError):
    """An edit's ``old_string`` does not occur in the text.

    ``near_miss`` is the same text found with different whitespace, if any, cut to
    300 characters.
    """

    def __init__(self, edit_index: int, near_miss: str | None) -> None:
        self.edit_index = edit_index
        self.near_miss = near_miss
        if near_miss is not None:
            self.hint = (
                "The text exists with different whitespace. Retry with the exact text in "
                f"near_miss. {ANCHOR_HINT}"
            )
            detail = f" It exists with different whitespace: {near_miss!r}."
        else:
            self.hint = ANCHOR_HINT
            detail = ""
        super().__init__(f"Edit {edit_index}: old_string not found.{detail} {ANCHOR_HINT}")


class AnchorAmbiguous(EditingError, LookupError):
    """An edit's ``old_string`` occurs more than once and ``replace_all`` is not set.

    ``matches`` holds excerpts around the first three occurrences.
    """

    def __init__(self, edit_index: int, count: int, matches: Sequence[str]) -> None:
        self.edit_index = edit_index
        self.count = count
        self.matches = list(matches)
        self.hint = (
            "Extend old_string with surrounding text so it matches once, "
            "or set replace_all to replace every occurrence."
        )
        super().__init__(f"Edit {edit_index}: old_string occurs {count} times. {self.hint}")


class ShrinkRefused(EditingError):
    """The edited text is less than half the size of the original."""

    def __init__(self, chars_before: int, chars_after: int) -> None:
        self.chars_before = chars_before
        self.chars_after = chars_after
        super().__init__(
            f"Refusing to write: the page would shrink from {chars_before} to {chars_after} "
            "characters, less than half its size. Set allow_shrink if this is intended."
        )


class PageStale(EditingError):
    """The page changed since the caller read it."""

    def __init__(self, page_updated_at: str, expected_updated_at: str) -> None:
        self.page_updated_at = page_updated_at
        self.expected_updated_at = expected_updated_at
        super().__init__(
            f"The page was updated at {page_updated_at}, not {expected_updated_at} as expected. "
            "Read the page again and rebuild the change."
        )
