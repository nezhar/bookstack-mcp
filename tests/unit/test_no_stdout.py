import importlib
import logging
import pkgutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import bookstack_mcp
from bookstack_mcp.config import Settings
from bookstack_mcp.logs import setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_importing_and_configuring_writes_nothing_to_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOOKSTACK_URL", "https://docs.example.com/api")
    monkeypatch.setenv("BOOKSTACK_TOKEN_ID", "id")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET", "secret")

    for module in pkgutil.walk_packages(bookstack_mcp.__path__, "bookstack_mcp."):
        importlib.import_module(module.name)
    settings = Settings.load()
    setup_logging(settings.log_level)
    for notice in settings.notices:
        logging.getLogger("bookstack_mcp").info(notice)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "/api" in captured.err
