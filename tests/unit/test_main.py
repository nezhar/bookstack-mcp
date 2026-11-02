"""The command line: --version, check, and how startup reads /api/system."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from bookstack_mcp import __main__ as cli
from bookstack_mcp import __version__
from bookstack_mcp.client import BookStackClient, RetryPolicy
from bookstack_mcp.config import Settings

BASE_URL = "https://docs.example.com"
SYSTEM = {
    "version": "v26.09.2",
    "instance_id": "abc",
    "app_name": "BookStack",
    "app_logo": None,
    "base_url": BASE_URL,
}

Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOOKSTACK_URL", BASE_URL)
    monkeypatch.setenv("BOOKSTACK_TOKEN_ID", "id")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET", "secret")


def settings() -> Settings:
    values: dict[str, Any] = {"url": BASE_URL, "token_id": "id", "token_secret": "secret"}
    return Settings(**values, _env_file=None)


def client_for(handler: Handler) -> BookStackClient:
    """Built like the startup probe: one attempt per call, so only the startup loop retries."""
    return BookStackClient(
        BASE_URL,
        "id",
        "secret",
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(max_attempts=1),
    )


def use_handler(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    monkeypatch.setattr(cli, "make_client", lambda _settings, **_kw: client_for(handler))


def run_main(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    code = exit_info.value.code
    return code if isinstance(code, int) else 1


def test_version_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_main(["--version"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == f"bookstack-mcp {__version__}"


def test_help_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_main(["--help"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "check" in captured.err


def test_missing_configuration_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for var in ("BOOKSTACK_URL", "BOOKSTACK_TOKEN_ID", "BOOKSTACK_TOKEN_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert run_main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "BOOKSTACK_URL: is required" in captured.err


def test_check_reports_to_stderr(
    env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(f"{request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/api/system":
            return httpx2.Response(200, json=SYSTEM)
        return httpx2.Response(200, json={"data": [], "total": 0})

    use_handler(monkeypatch, handler)
    assert run_main(["check"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "BookStack version: v26.09.2" in captured.err
    assert f"BookStack URL: {BASE_URL}" in captured.err
    assert "Toolsets: core" in captured.err
    assert "Read-only: no" in captured.err
    assert "Tools (" in captured.err
    assert seen == ["/api/system?", "/api/books?count=1"]


def test_check_fails_with_the_hint(
    env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_handler(
        monkeypatch,
        lambda _request: httpx2.Response(401, json={"error": {"message": "No token"}}),
    )
    assert run_main(["check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Check failed: No token" in captured.err
    assert "Hint: Check BOOKSTACK_TOKEN_ID" in captured.err


async def test_startup_retries_then_assumes_the_oldest_version(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli, "SYSTEM_RETRY_DELAY_S", 0)
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        raise httpx2.ConnectError("refused")

    async with client_for(handler) as client:
        system = await cli.startup_system(settings(), client)
    assert len(calls) == 3
    assert system.version == "v26.05.0"
    assert system.base_url == BASE_URL
    assert any(
        r.levelname == "WARNING" and "assuming the oldest supported" in r.getMessage()
        for r in caplog.records
    )


async def test_startup_does_not_retry_auth_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "SYSTEM_RETRY_DELAY_S", 0)
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(401, json={"error": {"message": "No token"}})

    async with client_for(handler) as client:
        system = await cli.startup_system(settings(), client)
    assert calls == ["/api/system"]
    assert system.version == "v26.05.0"


async def test_startup_retry_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "SYSTEM_RETRY_DELAY_S", 0)
    answers: list[Any] = [httpx2.Response(503), httpx2.Response(200, json=SYSTEM)]

    async with client_for(lambda _request: answers.pop(0)) as client:
        system = await cli.startup_system(settings(), client)
    assert system.version == "v26.09.2"


async def test_startup_warns_about_old_versions(caplog: pytest.LogCaptureFixture) -> None:
    old = SYSTEM | {"version": "v25.12.1"}
    async with client_for(lambda _request: httpx2.Response(200, json=old)) as client:
        system = await cli.startup_system(settings(), client)
    assert system.version == "v25.12.1"
    [warning] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "26.05 and 26.09" in warning.getMessage()
