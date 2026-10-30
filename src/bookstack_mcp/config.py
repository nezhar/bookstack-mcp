"""Settings read from environment variables and an optional `.env` file.

Every field is bound to one environment variable through `validation_alias`. To add a
setting, add a field with an alias; `Settings.load()` maps its validation errors to the
variable name automatically.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

__all__ = ["ConfigError", "LogLevel", "Settings", "Toolset"]


class ConfigError(Exception):
    """Invalid or missing configuration. The message never contains a setting's value."""


class Toolset(StrEnum):
    """Groups of tools that can be switched on with `BOOKSTACK_MCP_TOOLSETS`."""

    CORE = "core"
    STRUCTURE = "structure"
    COLLAB = "collab"
    FILES = "files"
    ADMIN = "admin"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# Secrets that also accept a `<NAME>_FILE` variable holding a path (Docker secrets).
_SECRET_VARS = ("BOOKSTACK_TOKEN_ID", "BOOKSTACK_TOKEN_SECRET")


class Settings(BaseSettings):
    """Process-wide settings. Build with `Settings.load()`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        validate_by_name=True,
    )

    url: str = Field(validation_alias="BOOKSTACK_URL")
    """Base URL of the BookStack instance, without a trailing `/` or `/api`."""

    token_id: SecretStr = Field(validation_alias="BOOKSTACK_TOKEN_ID")
    token_secret: SecretStr = Field(validation_alias="BOOKSTACK_TOKEN_SECRET")
    token_id_file: Path | None = Field(default=None, validation_alias="BOOKSTACK_TOKEN_ID_FILE")
    token_secret_file: Path | None = Field(
        default=None, validation_alias="BOOKSTACK_TOKEN_SECRET_FILE"
    )

    toolsets: Annotated[frozenset[Toolset], NoDecode] = Field(
        default=frozenset({Toolset.CORE}), validation_alias="BOOKSTACK_MCP_TOOLSETS"
    )
    read_only: bool = Field(default=False, validation_alias="BOOKSTACK_MCP_READ_ONLY")
    allow_delete: bool = Field(default=False, validation_alias="BOOKSTACK_MCP_ALLOW_DELETE")
    requests_per_minute: int = Field(
        default=150, ge=1, le=10000, validation_alias="BOOKSTACK_MCP_REQUESTS_PER_MINUTE"
    )
    timeout_seconds: float = Field(
        default=30, ge=1, le=600, validation_alias="BOOKSTACK_MCP_TIMEOUT_SECONDS"
    )
    max_read_chars: int = Field(
        default=20000, ge=1000, le=200000, validation_alias="BOOKSTACK_MCP_MAX_READ_CHARS"
    )
    verify_tls: bool = Field(default=True, validation_alias="BOOKSTACK_MCP_VERIFY_TLS")
    ca_bundle: Path | None = Field(default=None, validation_alias="BOOKSTACK_MCP_CA_BUNDLE")
    log_level: LogLevel = Field(default="INFO", validation_alias="BOOKSTACK_MCP_LOG_LEVEL")

    @classmethod
    def load(cls, env_file: str | Path | None = ".env") -> Self:
        """Read the settings, or raise `ConfigError` naming each broken variable and rule.

        `env_file` is read when it exists; pass `None` to read the environment only.
        """
        try:
            return cls(_env_file=env_file)
        except ValidationError as exc:
            raise ConfigError(_describe(cls, exc)) from None

    @property
    def notices(self) -> tuple[str, ...]:
        """Messages worth logging once logging is set up (the URL had a trailing `/api`)."""
        return self._notices

    @property
    def api_url(self) -> str:
        """Base URL of the REST API."""
        return f"{self.url}/api"

    # -- validation ----------------------------------------------------------------

    _notices: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _read_secret_files(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for var in _SECRET_VARS:
            file_var = f"{var}_FILE"
            field = var.removeprefix("BOOKSTACK_").lower()
            value_key = _find_key(data, var, field)
            file_key = _find_key(data, file_var, f"{field}_file")
            if file_key is None or data[file_key] in (None, ""):
                continue
            if value_key is not None and data[value_key] not in (None, ""):
                raise ValueError(f"{var} and {file_var} are both set; set only one")
            try:
                content = Path(data[file_key]).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError, TypeError, ValueError):
                raise ValueError(f"{file_var} must name a readable UTF-8 text file") from None
            data[var] = content
        return data

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        try:
            parts = urlsplit(value)
            _ = parts.port  # raises on a malformed port
        except ValueError:
            raise ValueError("must be a valid URL") from None
        if parts.scheme not in ("http", "https"):
            raise ValueError("must start with http:// or https://")
        if not parts.hostname:
            raise ValueError("must include a host name")
        if parts.username is not None or parts.password is not None:
            raise ValueError("must not contain a user name or password")
        if parts.query or "?" in value:
            raise ValueError("must not contain a query string")
        if parts.fragment or "#" in value:
            raise ValueError("must not contain a fragment")
        return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")

    @field_validator("token_id", "token_secret")
    @classmethod
    def _check_secret(cls, value: SecretStr) -> SecretStr:
        stripped = value.get_secret_value().strip()
        if not stripped:
            raise ValueError("must not be empty")
        return SecretStr(stripped)

    @field_validator("toolsets", mode="before")
    @classmethod
    def _split_toolsets(cls, value: Any) -> Any:
        if isinstance(value, str):
            names = [name.strip().lower() for name in value.split(",")]
            value = [name for name in names if name]
        if isinstance(value, list | tuple | set | frozenset):
            if not value:
                raise ValueError("must name at least one toolset")
            known = {t.value for t in Toolset}
            for name in value:
                if str(name) not in known:
                    allowed = ", ".join(t.value for t in Toolset)
                    raise ValueError(f"only these toolsets exist: {allowed}")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("ca_bundle")
    @classmethod
    def _check_ca_bundle(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_file():
            raise ValueError("must be the path of an existing PEM file")
        return value

    @model_validator(mode="after")
    def _strip_api_suffix(self) -> Self:
        if self.url.endswith("/api"):
            self.url = self.url.removesuffix("/api").rstrip("/")
            self._notices = ("BOOKSTACK_URL ends in /api; using the instance base URL instead",)
        return self


def _find_key(data: dict[str, Any], alias: str, field: str) -> str | None:
    """Find the input key for a variable; sources may lower-case it."""
    for key in data:
        if key.lower() in (alias.lower(), field):
            return key
    return None


def _describe(cls: type[Settings], exc: ValidationError) -> str:
    """Turn a validation error into `VAR: rule` lines without any input values."""
    names: dict[str, str] = {}
    for field_name, info in cls.model_fields.items():
        alias = info.validation_alias
        if isinstance(alias, str):
            names[field_name] = alias
            names[alias.lower()] = alias
    lines = []
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        msg = error["msg"].removeprefix("Value error, ")
        if error["type"] == "missing":
            msg = "is required"
        loc = error["loc"]
        if loc:
            var = names.get(str(loc[0]), names.get(str(loc[0]).lower(), str(loc[0])))
            lines.append(f"{var}: {msg}")
        else:
            lines.append(msg)
    return "invalid configuration: " + "; ".join(lines)
