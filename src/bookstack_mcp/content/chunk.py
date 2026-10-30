"""Split long content into windows a model can read one at a time."""

from dataclasses import dataclass

DEFAULT_MAX_CHARS = 20000


class InvalidChunkRequest(ValueError):
    """The offset or window size is out of range."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One window of the text; ``next_offset`` is ``None`` once the end is reached."""

    content: str
    offset: int
    returned_chars: int
    total_chars: int
    next_offset: int | None
    truncated: bool


def chunk(text: str, offset: int = 0, max_chars: int = DEFAULT_MAX_CHARS) -> Chunk:
    """Return up to ``max_chars`` characters of ``text`` starting at ``offset``.

    When the window ends before the text does, it is cut after the last blank line
    (``\\n\\n``) within its final 10%, if there is one, else at exactly ``max_chars``.
    Concatenating the chunks obtained by following ``next_offset`` gives back ``text``.
    """
    total = len(text)
    if max_chars < 1:
        raise InvalidChunkRequest("max_chars must be at least 1.")
    if not 0 <= offset <= total:
        raise InvalidChunkRequest(f"offset must be between 0 and {total} (the content length).")
    limit = offset + max_chars
    if limit >= total:
        end = total
    else:
        blank = text.rfind("\n\n", limit - max_chars // 10, limit)
        end = blank + 2 if blank != -1 else limit
    content = text[offset:end]
    truncated = end < total
    return Chunk(
        content=content,
        offset=offset,
        returned_chars=len(content),
        total_chars=total,
        next_offset=end if truncated else None,
        truncated=truncated,
    )
