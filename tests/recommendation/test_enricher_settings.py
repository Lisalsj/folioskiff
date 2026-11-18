from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
async def test_enricher_credentials_are_configured_without_values_entering_api_or_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.credentials import ENRICHER_CREDENTIAL_NAMES

    for name in (
        "OPENALEX_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "CROSSREF_CONTACT_EMAIL",
        "CROSSREF_PLUS_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    store = MemoryCredentialStore()
    paths = make_paths(tmp_path)
    app = create_app(paths=paths, bootstrap_token="secret", credential_store=store)
    api_key = "openalex-do-not-persist-123"

    async with local_client(app) as client:
        registry_identity = id(app.state.enricher_registry)
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": client.cookies["paper_reader_csrf"],
        }
        initial = await client.get("/api/v1/settings/enrichers")
        invalid_email = await client.put(
            "/api/v1/settings/enrichers/crossref/credentials/contact_email",
            json={"value": "not-an-email"},
            headers=headers,
        )
        saved = await client.put(
            "/api/v1/settings/enrichers/openalex/credentials/api_key",
            json={"value": api_key},
            headers=headers,
        )
        saved_email = await client.put(
            "/api/v1/settings/enrichers/crossref/credentials/contact_email",
            json={"value": "reader@example.org"},
            headers=headers,
        )
        capabilities = await client.get("/api/v1/settings/enrichers")
        removed = await client.delete(
            "/api/v1/settings/enrichers/openalex/credentials/api_key",
            headers=headers,
        )
        unsupported = await client.put(
            "/api/v1/settings/enrichers/openalex/credentials/password",
            json={"value": "ignored"},
            headers=headers,
        )
        assert id(app.state.enricher_registry) == registry_identity

    initial_openalex = next(
        item for item in initial.json() if item["enricher_id"] == "openalex"
    )
    assert initial_openalex["enabled"] is False
    assert initial_openalex["disabled_reason"] == "missing_api_key"
    assert initial_openalex["credentials"] == [
        {
            "credential_id": "api_key",
            "label": "OpenAlex API key",
            "required": True,
            "secret": True,
            "configured": False,
        }
    ]
    assert invalid_email.status_code == 422
    assert saved.status_code == 200
    assert saved.json()["enabled"] is True
    assert saved.json()["credentials"][0]["configured"] is True
    assert api_key not in saved.text
    assert saved_email.status_code == 200
    crossref = next(
        item for item in capabilities.json() if item["enricher_id"] == "crossref"
    )
    assert {item["credential_id"]: item["configured"] for item in crossref["credentials"]} == {
        "contact_email": True,
        "plus_api_token": False,
    }
    assert removed.json()["enabled"] is False
    assert unsupported.status_code == 409
    assert store.get(ENRICHER_CREDENTIAL_NAMES[("openalex", "api_key")]) is None
    for database_file in paths.state_root.glob("paper-reader.sqlite3*"):
        assert api_key.encode() not in database_file.read_bytes()


def test_enricher_vault_values_override_environment_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.credentials import (
        ENRICHER_CREDENTIAL_NAMES,
        resolve_source_credentials,
    )

    monkeypatch.setenv("OPENALEX_API_KEY", "environment-openalex")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "environment-s2")
    store = MemoryCredentialStore(
        {ENRICHER_CREDENTIAL_NAMES[("openalex", "api_key")]: "vault-openalex"}
    )

    resolved = resolve_source_credentials(store)

    assert resolved.openalex_api_key == "vault-openalex"
    assert resolved.semantic_scholar_api_key == "environment-s2"
