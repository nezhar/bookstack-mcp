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
