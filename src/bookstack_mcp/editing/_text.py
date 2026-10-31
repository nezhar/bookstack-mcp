# Behaviour adapted from pnocera/bookstack-mcp-server v2.1.1 (src/utils/page-content.ts),
# used under the MIT License:
#
# Copyright (c) 2025 BookStack MCP Server Contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

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
