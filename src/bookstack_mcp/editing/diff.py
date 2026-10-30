"""Short unified diff of a page edit, for previews and write summaries."""

import difflib

MAX_LINES = 40
MAX_CHARS = 4000


def summary(before: str, after: str) -> str:
    """Unified diff of the changed lines, without context lines or file headers.

    Cut to at most ``MAX_LINES`` lines and ``MAX_CHARS`` characters, with a final
    note saying how much was left out. Empty when nothing changed.
    """
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))[
        2:
    ]
    shown: list[str] = []
    size = 0
    for line in lines[:MAX_LINES]:
        room = MAX_CHARS - size - (1 if shown else 0)
        if len(line) > room:
            if room > 0:
                shown.append(line[:room])
            break
        shown.append(line)
        size += len(line) + (1 if len(shown) > 1 else 0)
    text = "\n".join(shown)
    complete = len(shown) == len(lines) and (not shown or shown[-1] == lines[len(shown) - 1])
    if not complete:
        text += (
            f"\n… diff cut: {len(lines)} lines in total, "
            f"limit {MAX_LINES} lines or {MAX_CHARS} characters"
        )
    return text
