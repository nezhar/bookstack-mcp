"""Command-line entry point for bookstack-mcp."""

import sys

from bookstack_mcp import __version__


def main() -> None:
    """Print the version to stderr; stdout is reserved for MCP messages."""
    print(f"bookstack-mcp {__version__}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
