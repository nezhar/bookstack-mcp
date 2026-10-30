# bookstack-mcp

An MCP (Model Context Protocol) server for a self-hosted [BookStack](https://www.bookstackapp.com/) instance. It lets MCP clients such as Claude Desktop and Claude Code search, read and edit BookStack shelves, books, chapters and pages through the BookStack API, using one API token configured per server process.

What an assistant can do with it:

- **Find:** full-text search with BookStack's query syntax (tags, filters, scoped to a book or chapter), browse shelves, books and chapters, list tags and recently changed pages.
- **Read:** pages as markdown, as a heading outline or as their stored source, in chunks, by section, or grep for exact text.
- **Write:** create pages, change part of a page by exact find-and-replace, append to a page or a section, rename, move, retag or replace a page. Every edit can be previewed with a dry run, refuses to overwrite a page changed since it was read, refuses to shrink a page by more than half unless told to, and re-reads the page to verify the write.

The [tool reference](docs/tools.md) lists every tool with its inputs. It is generated from the server, so it is always current.

Supported BookStack versions: 26.09 and 26.05. Status: pre-release.

Contributors and coding agents: see [AGENTS.md](AGENTS.md) for the layout, commands and rules.

## Quick start

### 1. Prepare BookStack

The assistant acts as a BookStack user, so it sees and changes exactly what that user may. Give it a user of its own:

1. **Create a role** for the assistant (Settings, Roles, Create New Role) with:
   - "Access system API" (`access-api`), which every API call needs.
   - "Change page editor" (`editor-change`), so pages the assistant writes as markdown open in the markdown editor.
   - View permissions on the shelves, books, chapters and pages it may read.
   - Create and update permissions only where it may write. Leave them out entirely for a read-only assistant, and leave delete permissions out.

   Asset permissions on the role apply everywhere; to limit the assistant to some books, grant them on those books' own permissions instead.
2. **Create a user** for the assistant (Settings, Users, Add New User) and give it only that role.
3. **Create an API token** on that user: Settings, Users, the user, API Tokens, Create Token. BookStack shows the token secret only once; copy the token ID and the secret.

Never commit the token. Keep it in your MCP client's configuration or in a git-ignored `.env` file.

### 2. Run it

Until the first PyPI release, run it with [uv](https://docs.astral.sh/uv/) straight from the repository:

```sh
uvx --from git+https://github.com/nezhar/bookstack-mcp@v0.1.0 bookstack-mcp
```

The server speaks MCP over stdio, so you normally let your MCP client start it, as below. It reads its settings from environment variables, or from a `.env` file in the working directory (see [`.env.example`](.env.example)).

### 3. Add it to Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit Config; on macOS it is in `~/Library/Application Support/Claude/`, on Windows in `%APPDATA%\Claude\`) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "bookstack": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/nezhar/bookstack-mcp@v0.1.0", "bookstack-mcp"],
      "env": {
        "BOOKSTACK_URL": "https://docs.example.com",
        "BOOKSTACK_TOKEN_ID": "…",
        "BOOKSTACK_TOKEN_SECRET": "…"
      }
    }
  }
}
```

If Claude Desktop cannot find `uvx`, put its full path in `command` (`which uvx` shows it).

### 4. Add it to Claude Code

```sh
claude mcp add bookstack \
  -e BOOKSTACK_URL=https://docs.example.com \
  -e BOOKSTACK_TOKEN_ID=… \
  -e BOOKSTACK_TOKEN_SECRET=… \
  -- uvx --from git+https://github.com/nezhar/bookstack-mcp@v0.1.0 bookstack-mcp
```

Keep the server name before the `-e` options (`-e` takes several values and would swallow it), and everything after `--` is the command that starts the server. The server is added for you in the current project; add `--scope user` before the name to use it in every project. Don't use `--scope project` with a token in the command: that scope writes `.mcp.json`, which is meant to be committed.

### 5. Check the connection

```sh
BOOKSTACK_URL=https://docs.example.com BOOKSTACK_TOKEN_ID=… BOOKSTACK_TOKEN_SECRET=… \
  uvx --from git+https://github.com/nezhar/bookstack-mcp@v0.1.0 bookstack-mcp check
```

`bookstack-mcp check` reads the same settings as the server (environment or `.env`), calls BookStack and prints the BookStack version, the toolsets, read-only mode and the tools this configuration offers. It exits with 0 when everything works, 1 when BookStack refused or could not be reached (with a hint), and 2 when the configuration is invalid.

## Configuration

All settings are environment variables; a `.env` file in the working directory is read too. Boolean values are `true` or `false`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BOOKSTACK_URL` | required | Base URL of the BookStack instance, such as `https://docs.example.com`, without `/api`. |
| `BOOKSTACK_TOKEN_ID` | required | ID of the API token. |
| `BOOKSTACK_TOKEN_SECRET` | required | Secret of the API token. |
| `BOOKSTACK_TOKEN_ID_FILE` | unset | Path of a file holding the token ID (for Docker secrets), instead of `BOOKSTACK_TOKEN_ID`. Set one or the other. |
| `BOOKSTACK_TOKEN_SECRET_FILE` | unset | Path of a file holding the token secret, instead of `BOOKSTACK_TOKEN_SECRET`. Set one or the other. |
| `BOOKSTACK_MCP_TOOLSETS` | `core` | Comma-separated toolsets to enable: `core`, `structure`, `collab`, `files`, `admin`. All current tools are in `core`; the tool reference shows each tool's toolset. |
| `BOOKSTACK_MCP_READ_ONLY` | `false` | `true` hides every tool that changes BookStack, whatever the token may do. |
| `BOOKSTACK_MCP_ALLOW_DELETE` | `false` | `true` shows destructive (delete) tools; they stay hidden otherwise, and always in read-only mode. |
| `BOOKSTACK_MCP_REQUESTS_PER_MINUTE` | `150` | Most API requests the server sends per minute (1 to 10000). Keep it under BookStack's `API_REQUESTS_PER_MIN` (default 180), which counts per user. |
| `BOOKSTACK_MCP_TIMEOUT_SECONDS` | `30` | Timeout of one API request, in seconds (1 to 600). |
| `BOOKSTACK_MCP_MAX_READ_CHARS` | `20000` | Characters `read_page` returns per call unless the model passes `max_chars` (1000 to 200000, used up to 100000); longer pages come in chunks. |
| `BOOKSTACK_MCP_VERIFY_TLS` | `true` | `false` skips TLS certificate checks. Prefer `BOOKSTACK_MCP_CA_BUNDLE` for a private CA. |
| `BOOKSTACK_MCP_CA_BUNDLE` | unset | Path of a PEM file with the CA certificates to trust for BookStack. |
| `BOOKSTACK_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Logs are JSON lines on stderr and never contain page content or secrets. |

Tools that need a newer BookStack than the connected one are hidden automatically; `bookstack-mcp check` shows what is left.

## Troubleshooting

Run `bookstack-mcp check` first: it shows BookStack's answer and a hint. Tool errors carry the same message to the assistant.

| Status | Meaning | Fix |
| --- | --- | --- |
| 401 | The token is wrong or expired. | Check `BOOKSTACK_TOKEN_ID` and `BOOKSTACK_TOKEN_SECRET`, or create a new token on the user. |
| 403 "does not have permission to make API calls" | The user's role lacks "Access system API". | Add "Access system API" to the assistant's role. |
| 403 on a write | The role may not create or update there. | Grant create or update permission on that book, chapter or page, or on the role. |
| 404 | The item does not exist, or the assistant's user may not see it. | Check the id, and the view permissions of the assistant's role on that item. |
| 429 | Rate limited by BookStack. | Lower `BOOKSTACK_MCP_REQUESTS_PER_MINUTE`, or raise `API_REQUESTS_PER_MIN` on the BookStack server. |

Pages written as markdown that open in the WYSIWYG editor mean the role lacks "Change page editor".
