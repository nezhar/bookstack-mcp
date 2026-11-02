import json
import logging
from collections.abc import Iterator

import pytest

from bookstack_mcp.logs import REDACTED, setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def last_entry(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    entry = json.loads(lines[-1])
    assert isinstance(entry, dict)
    return entry


def test_writes_one_json_object_per_line_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    log = logging.getLogger("bookstack_mcp.test")
    log.info("first")
    log.debug("hidden")
    log.warning("second %s", "arg")
    captured = capsys.readouterr()
    assert captured.out == ""
    entries = [json.loads(line) for line in captured.err.strip().splitlines()]
    assert [e["msg"] for e in entries] == ["first", "second arg"]
    assert entries[1]["level"] == "WARNING"
    assert entries[1]["logger"] == "bookstack_mcp.test"
    assert str(entries[1]["ts"]).endswith("Z")
    assert "T" in str(entries[1]["ts"])


def test_redacts_extras_outside_the_allowlist(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("DEBUG")
    sensitive = {
        "content": "page body text",
        "markdown": "# heading secret",
        "token": "tok-123",
        "authorization": "Token abc:def",
        "password": "hunter2",
    }
    allowed = {"tool": "read_page", "page_id": 42, "status": 200, "read_only": True}
    logging.getLogger("t").info("call", extra={**sensitive, **allowed})
    raw = capsys.readouterr().err
    for value in sensitive.values():
        assert value not in raw
    entry = json.loads(raw.strip().splitlines()[-1])
    for key in sensitive:
        assert entry[key] == REDACTED
    for key, value in allowed.items():
        assert entry[key] == value


def test_path_loses_its_query_string(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    logging.getLogger("t").info("req", extra={"path": "/api/pages?filter[name]=secret#x"})
    assert last_entry(capsys)["path"] == "/api/pages"


def test_exceptions_have_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    local_secret = "local-variable-value"  # noqa: S105
    try:
        raise RuntimeError("GET https://docs.example/api/search?query=confidential failed")
    except RuntimeError:
        logging.getLogger("t").exception("failed")
    captured = capsys.readouterr()
    assert local_secret not in captured.err
    assert "Traceback" not in captured.err
    assert "confidential" not in captured.err
    entry = json.loads(captured.err.strip().splitlines()[-1])
    assert entry["exc_type"] == "RuntimeError"
    assert "exc_message" not in entry


def test_setup_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    setup_logging("INFO")
    logging.getLogger("t").info("once")
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_http_library_loggers_are_quieted() -> None:
    setup_logging("DEBUG")
    assert not logging.getLogger("httpx2").isEnabledFor(logging.INFO)
