from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from paper_reader.settings import AppPaths


class MemoryCredentialStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


@pytest.mark.asyncio
async def test_source_key_is_saved_outside_database_never_returned_and_can_be_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.credentials import SOURCE_CREDENTIAL_NAMES

    monkeypatch.delenv("IEEE_XPLORE_API_KEY", raising=False)
    store = MemoryCredentialStore()
    paths = make_paths(tmp_path)
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        credential_store=store,
    )
    api_secret = "ieee-do-not-persist-123"

    async with local_client(app) as client:
        registry_identity = id(app.state.connector_registry)
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": csrf,
        }
        unsupported = await client.put(
            "/api/v1/settings/sources/arxiv/credential",
            json={"value": api_secret},
            headers=headers,
        )
        saved = await client.put(
            "/api/v1/settings/sources/ieee/credential",
            json={"value": api_secret},
            headers=headers,
        )
        capabilities = await client.get("/api/v1/settings/sources")
        removed = await client.delete(
            "/api/v1/settings/sources/ieee/credential",
            headers=headers,
        )
        assert id(app.state.connector_registry) == registry_identity

    assert unsupported.status_code == 409
    assert saved.status_code == 200
    assert saved.json()["enabled"] is True
    assert saved.json()["credential_configured"] is True
    assert api_secret not in saved.text
    assert "api_key" not in saved.json()
    assert store.get(SOURCE_CREDENTIAL_NAMES["ieee"]) is None
    ieee = next(item for item in capabilities.json() if item["connector_id"] == "ieee")
    assert ieee["enabled"] is True
    assert removed.status_code == 200
    assert removed.json()["enabled"] is False
    assert removed.json()["credential_configured"] is False
    for database_file in paths.state_root.glob("paper-reader.sqlite3*"):
        assert api_secret.encode() not in database_file.read_bytes()


def test_vault_credentials_override_environment_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.credentials import SOURCE_CREDENTIAL_NAMES, resolve_source_credentials

    monkeypatch.setenv("IEEE_XPLORE_API_KEY", "environment-secret")
    monkeypatch.setenv("SPRINGER_NATURE_API_KEY", "environment-springer")
    store = MemoryCredentialStore({SOURCE_CREDENTIAL_NAMES["ieee"]: "vault-secret"})

    resolved = resolve_source_credentials(store)

    assert resolved.ieee_api_key == "vault-secret"
    assert resolved.springer_nature_api_key == "environment-springer"


def test_keyring_store_uses_paper_reader_service_without_echoing_values() -> None:
    from paper_reader.credentials import SERVICE_NAME, KeyringCredentialStore

    with patch("paper_reader.credentials.keyring") as backend:
        backend.get_password.return_value = "  stored-secret  "
        store = KeyringCredentialStore()
        store.set("source.ieee.api_key", "  new-secret  ")
        loaded = store.get("source.ieee.api_key")
        store.delete("source.ieee.api_key")

    backend.set_password.assert_called_once_with(
        SERVICE_NAME,
        "source.ieee.api_key",
        "new-secret",
    )
    backend.delete_password.assert_called_once_with(SERVICE_NAME, "source.ieee.api_key")
    assert loaded == "stored-secret"
