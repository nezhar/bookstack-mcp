"""The markdown view of a page: its own markdown, or BookStack's markdown export of its HTML."""

import re

# A level-1 heading line followed by one blank line, at the very start of the export.
_EXPORT_TITLE = re.compile(r"\A# [^\r\n]*(?:\r?\n){2}")


def strip_export_title(export: str, name: str) -> str:
    """Remove the ``# <page name>`` line and blank line BookStack puts before a markdown export.

    ``GET /api/pages/{id}/export/markdown`` returns ``"# " + name + "\\n\\n" + body``.
    The exact prefix is cut when present; otherwise any level-1 heading line followed by a
    blank line at the start is cut (in case the name was escaped or normalised). The body
    is returned unchanged, so page include tags stay as written.
    """
    exact = f"# {name}\n\n"
    if export.startswith(exact):
        return export[len(exact) :]
    if match := _EXPORT_TITLE.match(export):
        return export[match.end() :]
    if export.startswith("# ") and "\n" not in export:
        return ""
    return export
