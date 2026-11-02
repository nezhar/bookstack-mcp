"""Smoke tests for the BookStack integration fixture."""

from collections.abc import Callable

import httpx2
import pytest

pytestmark = pytest.mark.integration


def test_admin_token_lists_books(admin_client: httpx2.Client) -> None:
    response = admin_client.get("/books", params={"count": 1})
    assert response.status_code == 200
    assert "data" in response.json()


def test_viewer_reads_books_but_cannot_create(viewer_client: httpx2.Client) -> None:
    assert viewer_client.get("/books", params={"count": 1}).status_code == 200
    response = viewer_client.post("/books", json={"name": "it-viewer-must-not-create"})
    assert response.status_code == 403


def test_system_reports_a_26_version(admin_client: httpx2.Client) -> None:
    response = admin_client.get("/system")
    assert response.status_code == 200
    assert response.json()["version"].startswith("v26.")


def test_sandbox_book_exists(admin_client: httpx2.Client, sandbox_book: int) -> None:
    response = admin_client.get(f"/books/{sandbox_book}")
    assert response.status_code == 200
    assert response.json()["name"].startswith("it-")


def test_tinker_reaches_the_database(tinker: Callable[[str], str]) -> None:
    output = tinker('echo "users=" . \\BookStack\\Users\\Models\\User::query()->count() . PHP_EOL;')
    assert "users=" in output
