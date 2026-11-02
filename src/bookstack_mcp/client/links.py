"""Web links to BookStack items, built on the ``base_url`` that ``GET /api/system`` reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Links:
    """Builds item URLs. Search results and book contents carry a ``url``; prefer it."""

    base_url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def page(self, page_id: int) -> str:
        """The page's permanent link, which survives renames and moves."""
        return f"{self.base_url}/link/{page_id}"

    def book(self, book_slug: str) -> str:
        return f"{self.base_url}/books/{book_slug}"

    def chapter(self, book_slug: str, chapter_slug: str) -> str:
        return f"{self.base_url}/books/{book_slug}/chapter/{chapter_slug}"

    def shelf(self, shelf_slug: str) -> str:
        return f"{self.base_url}/shelves/{shelf_slug}"

    @staticmethod
    def prefer(item: Any, fallback: str) -> str:
        """The item's own non-empty ``url`` if it has one, else ``fallback``."""
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                return url
        return fallback
