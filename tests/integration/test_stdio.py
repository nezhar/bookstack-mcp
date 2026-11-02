"""`uv run bookstack-mcp` over stdio: stdout carries nothing but JSON-RPC messages."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp_types import LATEST_PROTOCOL_VERSION

from .conftest import BookStackInstance

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 60.0


async def _send(process: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message).encode() + b"\n")
    await process.stdin.drain()


async def _read_until(
    process: asyncio.subprocess.Process, request_id: int, lines: list[bytes]
) -> dict[str, Any]:
    assert process.stdout is not None
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), TIMEOUT_S)
        if not line:
            raise AssertionError("the server closed stdout before answering")
        lines.append(line)
        message = json.loads(line)
        if message.get("id") == request_id:
            return dict(message)


async def test_stdout_carries_only_json_rpc(bookstack: BookStackInstance) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv must be on PATH"
    env = os.environ | {
        "BOOKSTACK_URL": bookstack.base_url,
        "BOOKSTACK_TOKEN_ID": bookstack.admin.token_id,
        "BOOKSTACK_TOKEN_SECRET": bookstack.admin.token_secret,
        "BOOKSTACK_MCP_TOOLSETS": "core,structure,collab,files,admin",
        "BOOKSTACK_MCP_LOG_LEVEL": "DEBUG",
    }
    process = await asyncio.create_subprocess_exec(
        uv,
        "run",
        "bookstack-mcp",
        cwd=REPO_ROOT,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    lines: list[bytes] = []
    try:
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "stdio-test", "version": "0"},
                },
            },
        )
        initialized = await _read_until(process, 1, lines)
        assert initialized["result"]["serverInfo"]["name"] == "bookstack-mcp"
        await _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        await _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = await _read_until(process, 2, lines)
        assert isinstance(listed["result"]["tools"], list)
    finally:
        assert process.stdin is not None
        process.stdin.close()
        try:
            rest, stderr = await asyncio.wait_for(process.communicate(), TIMEOUT_S)
        except TimeoutError:
            process.kill()
            rest, stderr = await process.communicate()

    lines.extend(line for line in rest.splitlines(keepends=True) if line.strip())
    for line in lines:
        message = json.loads(line)
        assert message.get("jsonrpc") == "2.0", line
    # Logs went to stderr as JSON lines.
    log_lines = [line for line in stderr.decode().splitlines() if line.startswith("{")]
    assert any(
        json.loads(line)["msg"] == "starting the MCP server over stdio" for line in log_lines
    )
