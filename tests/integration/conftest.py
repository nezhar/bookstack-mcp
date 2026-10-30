"""BookStack in Docker for the integration tests.

Tests marked ``integration`` are skipped unless ``RUN_INTEGRATION=1``. With it set, a missing
Docker or a stack that never comes up fails the run; it never turns into skips.

BookStack only creates API tokens through its web UI, so the fixture writes the token rows with
``php artisan tinker`` inside the container (approach from pnocera/bookstack-mcp-server, MIT).
All credentials below exist only in the throwaway test instance.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest

COMPOSE_FILE = Path(__file__).with_name("compose.yml")
PROJECT = "bsmcp-it"
BASE_URL = "http://localhost:6875"

BOOT_TIMEOUT = 300.0
BOOT_POLL = 3.0
PROVISION_ATTEMPTS = 5
PROVISION_DELAY = 5.0
MAX_RATE_LIMIT_RETRIES = 5

ADMIN_EMAIL = "admin@admin.com"
VIEWER_EMAIL = "viewer@example.test"
VIEWER_NAME = "MCP Viewer"
VIEWER_ROLE = "MCP Viewer"
VIEWER_PERMISSIONS = [
    "access-api",
    "bookshelf-view-all",
    "book-view-all",
    "chapter-view-all",
    "page-view-all",
]


@dataclass(frozen=True)
class Credentials:
    token_id: str
    token_secret: str

    @property
    def authorization(self) -> str:
        return f"Token {self.token_id}:{self.token_secret}"


ADMIN = Credentials("bsmcp-it-admin-0000000000000000", "bsmcp-it-admin-secret-000000000")
VIEWER = Credentials("bsmcp-it-viewer-000000000000000", "bsmcp-it-viewer-secret-00000000")


@dataclass(frozen=True)
class BookStackInstance:
    base_url: str
    admin: Credentials
    viewer: Credentials

    def client(self, credentials: Credentials) -> httpx2.Client:
        """An HTTP client for ``{base_url}/api`` authenticated as ``credentials``."""
        return httpx2.Client(
            base_url=f"{self.base_url}/api",
            headers={"Authorization": credentials.authorization, "Accept": "application/json"},
            timeout=30.0,
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="integration test; set RUN_INTEGRATION=1 (needs Docker)")
    for item in items:
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip)


def _compose(*args: str, check: bool = True, timeout: float = 600.0) -> str:
    """Run ``docker compose`` for the test project and return its stdout."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", PROJECT, *args]
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"{' '.join(cmd)} exited with {result.returncode}:\n{result.stdout}\n{result.stderr}",
            pytrace=False,
        )
    return result.stdout


def run_tinker(php: str) -> str:
    """Run a PHP snippet through ``artisan tinker`` in the BookStack container.

    For facts the API doesn't expose, such as revision notes. Returns stdout and stderr.
    """
    cmd = [
        "docker", "compose", "-f", str(COMPOSE_FILE), "-p", PROJECT,
        "exec", "-T", "bookstack", "php", "/app/www/artisan", "tinker", f"--execute={php}",
    ]  # fmt: skip
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        cmd, capture_output=True, text=True, timeout=120, check=False
    )
    return result.stdout + result.stderr


def _token_php(email: str, credentials: Credentials) -> str:
    # forceFill: ApiToken only mass-assigns name and expires_at, so create()/fill() would save a
    # token without token_id/secret/user_id that never authenticates. The secret is stored hashed.
    return f"""
$user = \\BookStack\\Users\\Models\\User::query()->where("email", "=", "{email}")->first();
$token = \\BookStack\\Api\\ApiToken::query()
    ->where("token_id", "=", "{credentials.token_id}")->first()
    ?: new \\BookStack\\Api\\ApiToken();
$token->forceFill([
  "name" => "bsmcp integration tests",
  "token_id" => "{credentials.token_id}",
  "secret" => \\Illuminate\\Support\\Facades\\Hash::make("{credentials.token_secret}"),
  "user_id" => $user->id,
  "expires_at" => \\Illuminate\\Support\\Carbon::now()->addYears(5)->format("Y-m-d"),
])->save();
echo "PROVISION_OK id=" . $token->id . PHP_EOL;
"""


def _provision_token(email: str, credentials: Credentials) -> None:
    output = ""
    for attempt in range(PROVISION_ATTEMPTS):
        output = run_tinker(_token_php(email, credentials))
        if "PROVISION_OK" in output:
            return
        if attempt + 1 < PROVISION_ATTEMPTS:
            time.sleep(PROVISION_DELAY)
    pytest.fail(
        f"provisioning the API token for {email} failed after {PROVISION_ATTEMPTS} attempts:\n"
        f"{output}",
        pytrace=False,
    )


def api(client: httpx2.Client, method: str, path: str, **kwargs: Any) -> httpx2.Response:
    """Send a request, waiting out 429 responses for ``Retry-After`` seconds."""
    response = client.request(method, path, **kwargs)
    for _ in range(MAX_RATE_LIMIT_RETRIES):
        if response.status_code != 429:
            break
        time.sleep(float(response.headers.get("Retry-After", "5")))
        response = client.request(method, path, **kwargs)
    return response


def _expect(response: httpx2.Response, status: int) -> Any:
    if response.status_code != status:
        pytest.fail(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}, expected {status}: {response.text[:500]}",
            pytrace=False,
        )
    return response.json()


def _wait_for_boot() -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT
    last = "no response"
    while time.monotonic() < deadline:
        try:
            response = httpx2.get(f"{BASE_URL}/", timeout=10.0)
        except httpx2.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 500:
                return
            last = f"HTTP {response.status_code}"
        time.sleep(BOOT_POLL)
    pytest.fail(
        f"BookStack did not answer at {BASE_URL}/ within {BOOT_TIMEOUT:.0f}s (last: {last})",
        pytrace=False,
    )


def _ensure_viewer(admin: httpx2.Client) -> None:
    roles = _expect(api(admin, "GET", "/roles", params={"filter[display_name]": VIEWER_ROLE}), 200)
    if roles["data"]:
        role_id = roles["data"][0]["id"]
    else:
        role = _expect(
            api(
                admin,
                "POST",
                "/roles",
                json={"display_name": VIEWER_ROLE, "permissions": VIEWER_PERMISSIONS},
            ),
            200,
        )
        role_id = role["id"]

    users = _expect(api(admin, "GET", "/users", params={"filter[email]": VIEWER_EMAIL}), 200)
    if not users["data"]:
        _expect(
            api(
                admin,
                "POST",
                "/users",
                json={
                    "name": VIEWER_NAME,
                    "email": VIEWER_EMAIL,
                    "password": secrets.token_urlsafe(18),  # 24 characters
                    "roles": [role_id],
                },
            ),
            200,
        )


def _dump_logs(config: pytest.Config) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    logs = _compose("logs", "bookstack", check=False)
    if reporter is not None:
        reporter.write_sep("-", "docker compose logs bookstack")
        reporter.write_line(logs)


@pytest.fixture(scope="session")
def bookstack(request: pytest.FixtureRequest) -> Iterator[BookStackInstance]:
    if shutil.which("docker") is None:
        pytest.fail("RUN_INTEGRATION=1 but the docker CLI is not on PATH", pytrace=False)

    _compose("up", "-d")
    _wait_for_boot()

    instance = BookStackInstance(base_url=BASE_URL, admin=ADMIN, viewer=VIEWER)
    _provision_token(ADMIN_EMAIL, ADMIN)
    with instance.client(ADMIN) as admin:
        _expect(api(admin, "GET", "/books", params={"count": 1}), 200)
        _ensure_viewer(admin)
    _provision_token(VIEWER_EMAIL, VIEWER)

    yield instance

    if os.environ.get("BSMCP_IT_DOWN") == "1":
        # The stack is about to go; keep the BookStack logs of a failed run.
        if request.session.testsfailed:
            _dump_logs(request.config)
        _compose("down", "-v", check=False)


@pytest.fixture(scope="session")
def admin_client(bookstack: BookStackInstance) -> Iterator[httpx2.Client]:
    with bookstack.client(bookstack.admin) as client:
        yield client


@pytest.fixture(scope="session")
def viewer_client(bookstack: BookStackInstance) -> Iterator[httpx2.Client]:
    with bookstack.client(bookstack.viewer) as client:
        yield client


@pytest.fixture(scope="session")
def tinker(bookstack: BookStackInstance) -> Callable[[str], str]:
    """``tinker(php) -> str``: run PHP through artisan tinker in the running instance."""
    return run_tinker


@pytest.fixture
def sandbox_book(admin_client: httpx2.Client) -> Iterator[int]:
    """A fresh book named ``it-<uuid4 hex>``, deleted after the test."""
    book = _expect(
        api(admin_client, "POST", "/books", json={"name": f"it-{uuid.uuid4().hex}"}), 200
    )
    book_id = int(book["id"])
    yield book_id
    api(admin_client, "DELETE", f"/books/{book_id}")
