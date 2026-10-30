from pathlib import Path

import pytest

from bookstack_mcp.config import ConfigError, Settings, Toolset

SECRET = "s3cr3t-value-never-shown"  # noqa: S105


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start from an empty BookStack environment in a directory without a .env file."""
    import os

    for name in list(os.environ):
        if name.startswith("BOOKSTACK_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKSTACK_URL", "https://docs.example.com")
    monkeypatch.setenv("BOOKSTACK_TOKEN_ID", "token-id")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET", SECRET)


def load_error() -> str:
    with pytest.raises(ConfigError) as info:
        Settings.load()
    return str(info.value)


@pytest.mark.usefixtures("base_env")
def test_defaults() -> None:
    settings = Settings.load()
    assert settings.url == "https://docs.example.com"
    assert settings.api_url == "https://docs.example.com/api"
    assert settings.token_id.get_secret_value() == "token-id"
    assert settings.token_secret.get_secret_value() == SECRET
    assert settings.toolsets == frozenset({Toolset.CORE})
    assert settings.read_only is False
    assert settings.allow_delete is False
    assert settings.requests_per_minute == 150
    assert settings.timeout_seconds == 30
    assert settings.max_read_chars == 20000
    assert settings.verify_tls is True
    assert settings.ca_bundle is None
    assert settings.log_level == "INFO"
    assert settings.notices == ()
    assert SECRET not in repr(settings)


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://docs.example.com/api/", "https://docs.example.com"),
        ("https://docs.example.com/api", "https://docs.example.com"),
        ("https://docs.example.com/", "https://docs.example.com"),
        ("http://localhost:6875/wiki/", "http://localhost:6875/wiki"),
        ("https://x.example/wiki/api", "https://x.example/wiki"),
    ],
)
def test_url_is_normalised(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    monkeypatch.setenv("BOOKSTACK_URL", raw)
    assert Settings.load().url == expected


@pytest.mark.usefixtures("base_env")
def test_api_suffix_leaves_a_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKSTACK_URL", "https://docs.example.com/api/")
    notices = Settings.load().notices
    assert len(notices) == 1
    assert "/api" in notices[0]


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize(
    "raw",
    ["https://user:pw@x", "https://x?a=1", "https://x#frag", "ftp://x", "https://", "x"],
)
def test_bad_urls_are_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("BOOKSTACK_URL", raw)
    message = load_error()
    assert "BOOKSTACK_URL" in message
    assert raw not in message


@pytest.mark.usefixtures("base_env")
def test_empty_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKSTACK_URL", "")
    assert "BOOKSTACK_URL: is required" in load_error()


def test_missing_required_variables_are_named() -> None:
    message = load_error()
    for name in ("BOOKSTACK_URL", "BOOKSTACK_TOKEN_ID", "BOOKSTACK_TOKEN_SECRET"):
        assert name in message


@pytest.mark.usefixtures("base_env")
def test_blank_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET", "   ")
    assert "BOOKSTACK_TOKEN_SECRET: must not be empty" in load_error()


@pytest.mark.usefixtures("base_env")
def test_secret_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret_file = tmp_path / "secret"
    secret_file.write_text(f"  {SECRET}\n", encoding="utf-8")
    id_file = tmp_path / "id"
    id_file.write_text("id-from-file\n", encoding="utf-8")
    monkeypatch.delenv("BOOKSTACK_TOKEN_SECRET")
    monkeypatch.delenv("BOOKSTACK_TOKEN_ID")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("BOOKSTACK_TOKEN_ID_FILE", str(id_file))
    settings = Settings.load()
    assert settings.token_secret.get_secret_value() == SECRET
    assert settings.token_id.get_secret_value() == "id-from-file"


@pytest.mark.usefixtures("base_env")
def test_secret_and_file_both_set_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret_file = tmp_path / "secret"
    secret_file.write_text("other-secret", encoding="utf-8")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET_FILE", str(secret_file))
    message = load_error()
    assert "BOOKSTACK_TOKEN_SECRET and BOOKSTACK_TOKEN_SECRET_FILE are both set" in message
    assert SECRET not in message
    assert "other-secret" not in message


@pytest.mark.usefixtures("base_env")
def test_unreadable_secret_file_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BOOKSTACK_TOKEN_SECRET")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET_FILE", str(tmp_path / "missing"))
    assert "BOOKSTACK_TOKEN_SECRET_FILE must name a readable" in load_error()


@pytest.mark.usefixtures("base_env")
def test_empty_secret_file_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret_file = tmp_path / "secret"
    secret_file.write_text("\n", encoding="utf-8")
    monkeypatch.delenv("BOOKSTACK_TOKEN_SECRET")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET_FILE", str(secret_file))
    assert "BOOKSTACK_TOKEN_SECRET: must not be empty" in load_error()


@pytest.mark.usefixtures("base_env")
def test_error_messages_never_contain_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKSTACK_MCP_REQUESTS_PER_MINUTE", "0")
    monkeypatch.setenv("BOOKSTACK_MCP_LOG_LEVEL", SECRET)
    message = load_error()
    assert "BOOKSTACK_MCP_REQUESTS_PER_MINUTE" in message
    assert "BOOKSTACK_MCP_LOG_LEVEL" in message
    assert SECRET not in message


@pytest.mark.usefixtures("base_env")
def test_toolsets_parse_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKSTACK_MCP_TOOLSETS", " Core, structure,,files ")
    assert Settings.load().toolsets == {Toolset.CORE, Toolset.STRUCTURE, Toolset.FILES}


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize("raw", ["core,wiki", ",", "bookshelves"])
def test_unknown_toolsets_are_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("BOOKSTACK_MCP_TOOLSETS", raw)
    assert "BOOKSTACK_MCP_TOOLSETS" in load_error()


@pytest.mark.usefixtures("base_env")
def test_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("BOOKSTACK_MCP_READ_ONLY", "true")
    monkeypatch.setenv("BOOKSTACK_MCP_ALLOW_DELETE", "1")
    monkeypatch.setenv("BOOKSTACK_MCP_REQUESTS_PER_MINUTE", "10000")
    monkeypatch.setenv("BOOKSTACK_MCP_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("BOOKSTACK_MCP_MAX_READ_CHARS", "1000")
    monkeypatch.setenv("BOOKSTACK_MCP_VERIFY_TLS", "false")
    monkeypatch.setenv("BOOKSTACK_MCP_CA_BUNDLE", str(bundle))
    monkeypatch.setenv("BOOKSTACK_MCP_LOG_LEVEL", "debug")
    settings = Settings.load()
    assert settings.read_only is True
    assert settings.allow_delete is True
    assert settings.requests_per_minute == 10000
    assert settings.timeout_seconds == 600
    assert settings.max_read_chars == 1000
    assert settings.verify_tls is False
    assert settings.ca_bundle == bundle
    assert settings.log_level == "DEBUG"


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("BOOKSTACK_MCP_REQUESTS_PER_MINUTE", "10001"),
        ("BOOKSTACK_MCP_TIMEOUT_SECONDS", "0"),
        ("BOOKSTACK_MCP_TIMEOUT_SECONDS", "601"),
        ("BOOKSTACK_MCP_MAX_READ_CHARS", "999"),
        ("BOOKSTACK_MCP_MAX_READ_CHARS", "200001"),
        ("BOOKSTACK_MCP_READ_ONLY", "maybe"),
        ("BOOKSTACK_MCP_LOG_LEVEL", "TRACE"),
        ("BOOKSTACK_MCP_CA_BUNDLE", "/nonexistent/ca.pem"),
    ],
)
def test_out_of_range_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str
) -> None:
    monkeypatch.setenv(name, raw)
    assert f"{name}:" in load_error()


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "BOOKSTACK_URL=https://docs.example.com/\n"
        "BOOKSTACK_TOKEN_ID=id\n"
        f"BOOKSTACK_TOKEN_SECRET={SECRET}\n"
        "BOOKSTACK_MCP_TOOLSETS=core,admin\n",
        encoding="utf-8",
    )
    settings = Settings.load()
    assert settings.url == "https://docs.example.com"
    assert settings.toolsets == {Toolset.CORE, Toolset.ADMIN}


def test_environment_wins_over_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "BOOKSTACK_URL=https://from-file.example\nBOOKSTACK_TOKEN_ID=id\n"
        "BOOKSTACK_TOKEN_SECRET=x\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOOKSTACK_URL", "https://from-env.example")
    assert Settings.load().url == "https://from-env.example"


def test_dotenv_can_be_skipped(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("BOOKSTACK_URL=https://from-file.example\n", encoding="utf-8")
    assert "BOOKSTACK_URL: is required" in load_error_without_dotenv()


def load_error_without_dotenv() -> str:
    with pytest.raises(ConfigError) as info:
        Settings.load(env_file=None)
    return str(info.value)
