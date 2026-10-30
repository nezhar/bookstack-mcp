"""Small text helpers shared by the editing modules."""

import re

_TAG = re.compile(r"<[^>]*>")
_ENTITY = re.compile(r"&(?:nbsp|lt|gt|quot|#39|#039|amp);")
_ENTITIES = {
    "&nbsp;": " ",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&#039;": "'",
    "&amp;": "&",
}
_SPACE = re.compile(r"\s+")


def plain_text(fragment: str) -> str:
    """Strip tags, decode the common entities in one pass, and collapse whitespace."""
    text = _TAG.sub("", fragment)
    text = _ENTITY.sub(lambda m: _ENTITIES[m.group(0)], text)
    return _SPACE.sub(" ", text).strip()
