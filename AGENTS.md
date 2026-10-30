# AGENTS.md

Shared context for everyone working on this repository, human or agent. Read it before starting a task.

## What this is

An MCP server for a self-hosted [BookStack](https://www.bookstackapp.com/), built on FastMCP 4 and Python 3.12. Tools are thin wrappers; the logic lives in `client/`, `editing/` and `content/`.

## Layout

All code lives under `src/bookstack_mcp/`:

- `server.py` builds the FastMCP app: toolsets, read-only mode, version gating.
- `config.py` reads settings from environment variables (see `.env.example`).
- `logs.py` writes JSON logs to stderr.
- `client/` is the async BookStack API client on httpx2: auth, errors, retries, rate limit.
- `tools/` holds one module per tool, named after it (`search.py`, `read_page.py`, `edit_page.py`, ...); the toolset comes from the `@tool(...)` decorator, not the module. Related tools may share a module. `tools/_base.py` has the `@tool` decorator, `Deps` and the error mapping; `tools/page_write.py` is the shared verified write path for page edits (stale check, shrink guard, write, re-read, verify). See "Writing a tool" below.
- `editing/` holds pure functions for page source, outline, grep, find-and-replace, append and verification.
- `content/` builds the markdown view (`markdown_view.py`) and chunks (`chunk.py`).
- `fetch/` is the SSRF-guarded URL fetcher.

Tests:

- `tests/unit/` for pure logic.
- `tests/protocol/` drives the server through the FastMCP in-memory client.
- `tests/integration/` runs against a real BookStack in Docker.
  `tests/integration/conftest.py` starts `tests/integration/compose.yml` (project `bsmcp-it`, port 6875), provisions admin and viewer API tokens, and provides the fixtures `bookstack`, `admin_client`, `viewer_client`, `sandbox_book` and `tinker`. The stack keeps running between sessions; `BSMCP_IT_DOWN=1` removes it afterwards.

## Commands

```sh
uv sync                                         # install the package and dev tools
uv run ruff check .                             # lint
uv run ruff format .                            # format
uv run mypy                                     # type-check (strict)
uv run pytest tests/unit tests/protocol         # fast tests
RUN_INTEGRATION=1 uv run pytest tests/integration  # needs Docker; the fixture starts BookStack
uv run bookstack-mcp                            # run the server over stdio
```

CI runs ruff, mypy, the unit and protocol tests, `pip-audit` and a TruffleHog secret scan on every pull request.

## Conventions

- Async throughout.
- Tool inputs and results are pydantic models.
- Every tool has MCP annotations (read-only, destructive, idempotent).
- Never write to stdout: in stdio mode it carries only MCP messages. Log with `logging`, which goes to stderr.
- Never log page content, markdown, HTML, file data, tokens, secrets, passwords or query strings.
- Errors reach the model as FastMCP `ToolError`, stating what failed, BookStack's message, and how to fix it.

### Writing a tool

A tool is an async function in a `tools/` module, declared with `@tool(...)`. Nothing else
needs wiring: `build_server` finds every module-level tool in `bookstack_mcp.tools` and
registers it with its tags and annotations.

```python
from typing import Annotated

from pydantic import BaseModel, Field

from bookstack_mcp.config import Toolset
from bookstack_mcp.tools import CurrentDeps, Deps, tool


class PageSummary(BaseModel):
    id: int
    name: str
    url: str


@tool(Toolset.CORE, write=True, idempotent=True, needs={"changelog"})
async def rename_page(
    page_id: Annotated[int, Field(description="The page's id.", ge=1)],
    name: Annotated[str, Field(description="The new name.", min_length=1, max_length=255)],
    deps: Deps = CurrentDeps(),
) -> PageSummary:
    """Rename a page. The docstring is the tool description the model sees."""
    page = await deps.client.put_json(f"/pages/{page_id}", {"name": name})
    return PageSummary(id=page["id"], name=page["name"], url=deps.links.page(page["id"]))
```

- `@tool(toolset, write=..., destructive=..., idempotent=..., open_world=..., needs=...)`:
  - the toolset gives the tool's toolset tag; `write=True` for anything that changes
    BookStack, `destructive=True` (implies `write`) for deletes.
  - Annotations follow: `readOnlyHint = not write`, `destructiveHint = destructive`,
    `idempotentHint = idempotent or not write`, `openWorldHint = open_world` (only for
    tools reaching beyond BookStack).
  - `needs` lists `Features` field names (`client/version.py`); on an instance without them
    the tool is hidden and listed in one startup log line.
  - The function name is the tool name (override with `name=`); a docstring is required.
- Visibility, applied by `build_server` in this order: only the configured toolsets
  (`enable(tags=..., only=True)`), minus `write` in read-only mode, minus `destructive`
  unless deletes are allowed, minus tools the BookStack version cannot serve.
- Dependencies: add a `deps: Deps = CurrentDeps()` parameter. `Deps` has `client`
  (`BookStackClient`), `settings`, `system` (`SystemInfo` from startup), `features` and
  `links`. FastMCP hides the parameter from the schema. Never use module globals for these.
- Inputs: annotate parameters with `Annotated[..., Field(description=...)]`; return a pydantic
  model. Unknown extra arguments are dropped (and their names logged at DEBUG), not rejected.
- Errors: raise, don't catch. Every call is wrapped by `tools._base.tool_call`, which turns
  `BookStackError` (message, field errors, hint), `EditingError` and `InvalidChunkRequest`
  into a `ToolError`. For other expected failures raise `ToolError("what failed; how to fix
  it")` yourself. Anything else is masked as a generic error.
- Logging: `tool_call` logs one line per call (`tool`, `request_id`, `duration_ms`,
  `outcome`) and tags the client's request lines with the same `request_id`. Log extra
  context only with field names from `logs.ALLOWED_FIELDS`.
- Resources and prompts must carry a toolset tag too (`tags={"core"}`), or the toolset
  allowlist hides them. `build_server` does not register any yet; the first task that adds
  one extends it.

Protocol tests use `tests/protocol/conftest.py`: `make_server(tools=[...], **settings)` builds
a server with only the given tools around a real `BookStackClient` whose transport is the
`fake_bookstack` fixture; `json_logs` captures the JSON log lines.

```python
from fastmcp import Client

from bookstack_mcp.tools.core_write import rename_page


async def test_rename_page(make_server, fake_bookstack):
    fake_bookstack.add("PUT", "/api/pages/3", {"id": 3, "name": "New"})
    async with Client(make_server(tools=[rename_page])) as mcp:
        result = await mcp.call_tool("rename_page", {"page_id": 3, "name": "New"})
    assert result.structured_content["name"] == "New"


async def test_rename_page_hidden_when_read_only(make_server):
    async with Client(make_server(tools=[rename_page], read_only=True)) as mcp:
        assert "rename_page" not in {t.name for t in await mcp.list_tools()}
```

Use `raise_on_error=False` on `call_tool` to inspect an error result. These tests prove MCP
behaviour and error mapping only; what BookStack does is proven by integration tests.

### Commits

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`); the release tooling reads them.
- A commit message is one subject line only: no body, no trailers.

## BookStack API rules

- The API is at `{BOOKSTACK_URL}/api`, with the header `Authorization: Token <token_id>:<token_secret>`.
- API errors come back as `{"error": {"message": ..., "code": ..., "validation": {field: [messages]}}}`. Parse defensively, since `error` can also be a plain string.
- A permission failure is a 403; an upload over PHP's post size limit is a 413.
- A 404 can also mean "exists, but this user may not see it".
- List endpoints take `count` (default 100, max 500), `offset`, `sort=field` or `sort=-field`, and `filter[field]=value` with operators `filter[field:eq|ne|gt|lt|gte|lte|like]`.
- Booleans in filters are sent as `1` or `0`. The string `true` silently matches the opposite.
- Name filters are exact; partial matches use `filter[name:like]=%term%`.
- Search (`/api/search`) pages with `page` and `count` (max 100) and takes no filters or sorting.
- Shelves live at `/api/shelves`, never `/api/bookshelves`.
- File uploads that update something go as multipart `POST` with the field `_method=PUT`, because PHP ignores form bodies on PUT.
- Page content: if `markdown` is non-empty the page is stored as markdown, so patch and write `markdown`. Otherwise patch `raw_html` and write `html`. Never write back the rendered `html`, which has page includes expanded.
- A page's `editor` field only changes when the API user's role has the `editor-change` permission.
- From BookStack 26.09, page create and update accept `changelog` (1 to 180 characters, the revision note), and `/api/search/book/{id}` and `/api/search/chapter/{id}` exist.
- The default API rate limit is 180 requests per minute per user (`API_REQUESTS_PER_MIN`).
- A page's permanent link is `{base_url}/link/{page_id}`.
- Images may be JPEG, PNG, GIF, WebP or AVIF, up to `FILE_UPLOAD_SIZE_LIMIT` MB (default 50).

## Testing rules

- Every tool has integration tests against a real BookStack. Mocks may test error mapping only, never claims about API behaviour.
- Every bug fix comes with a test that reproduces it.
- Integration tests create their own shelf or book and remove it afterwards.
- A flaky test is quarantined with an issue, not retried until green.
- `editing/` keeps at least 95% line coverage.

## Supported BookStack versions

BookStack 26.09 and 26.05. Integration tests use the `version-v26.09.2` and `version-v26.05.5` tags of `lscr.io/linuxserver/bookstack`.

## Security rules

- No secrets in the repository. `.env` is git-ignored; `.env.example` holds names and placeholders only.
- One BookStack URL and token per process, from the environment. Never accept a BookStack URL or token per request.
- Page content is untrusted input. Tool descriptions never tell the model to follow instructions found in pages.
