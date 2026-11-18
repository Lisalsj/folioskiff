from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.database import create_session_factory, create_sqlite_engine
from paper_reader.plugins.catalog import PluginCatalogIssue, PluginCatalogRefresh
from paper_reader.plugins.contracts import PluginManifest
from paper_reader.plugins.discovery import DiscoveredEntryPoint, PluginInstallationRegistry
from paper_reader.plugins.repository import PluginHealthRepository
from paper_reader.plugins.sandbox import SandboxProbe
from paper_reader.settings import AppPaths


class FakeSandboxLauncher:
    def __init__(self, *, available: bool = True, code: str | None = None) -> None:
        self.probe_result = SandboxProbe(
            available=available,
            code=code,
            app_container=available,
            job_object=available,
        )

    def probe(self) -> SandboxProbe:
        return self.probe_result

    async def run(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError((args, kwargs))


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def bootstrap(client: httpx.AsyncClient, token: str = "one-time-secret") -> str:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": token},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return client.cookies["paper_reader_csrf"]


def mutation_headers(csrf: str) -> dict[str, str]:
    return {"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf}


def manifest_payload(
    *,
    plugin_id: str = "fixture.catalog",
    compatible: bool = True,
) -> dict[str, object]:
    protocol = (
        {"minimum": "1.0", "maximum": "1.0"}
        if compatible
        else {"minimum": "2.0", "maximum": "2.0"}
    )
    return {
        "manifest_version": "1",
        "plugin_id": plugin_id,
        "display_name": "Fixture Catalog",
        "distribution_name": "paper-reader-fixture-catalog",
        "distribution_version": "2.1.0",
        "protocol": protocol,
        "license": "Apache-2.0",
        "config_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "capabilities": [
            {
                "capability_id": f"{plugin_id}.source",
                "kind": "source_connector",
                "methods": ["probe", "estimate", "fetch_page"],
                "permissions": [
                    {
                        "kind": "network",
                        "resource": "api.example.org",
                        "purpose": "Read public fixture metadata",
                    },
                    {
                        "kind": "credential",
                        "resource": "fixture_api_key",
                        "purpose": "Authenticate through the host credential broker",
                    },
                ],
                "timeout_seconds": 30,
            },
            {
                "capability_id": f"{plugin_id}.score",
                "kind": "score_dimension",
                "methods": ["probe", "evaluate"],
                "permissions": [],
                "timeout_seconds": 20,
            },
        ],
    }


def install(
    app: Any,
    *,
    plugin_id: str = "fixture.catalog",
    compatible: bool = True,
) -> tuple[PluginInstallationRegistry, PluginManifest]:
    registry = PluginInstallationRegistry(app.state.sessions)
    manifest = PluginManifest.model_validate(
        manifest_payload(plugin_id=plugin_id, compatible=compatible)
    )
    registry.upsert(
        DiscoveredEntryPoint(
            plugin_id=plugin_id,
            entry_point_value="fixture_catalog.plugin:application",
            distribution_name="paper-reader-fixture-catalog",
            distribution_version="2.1.0",
        ),
        manifest,
        discovered_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
    )
    return registry, manifest


@pytest.mark.asyncio
async def test_plugin_list_requires_session_and_exposes_only_safe_management_fields(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="one-time-secret",
        plugin_sandbox_launcher=FakeSandboxLauncher(),
    )
    install(app)

    async with local_client(app) as client:
        unauthorized = await client.get("/api/v1/plugins")
        await bootstrap(client)
        response = await client.get("/api/v1/plugins")

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    plugin = payload[0]
    assert plugin == {
        "plugin_id": "fixture.catalog",
        "display_name": "Fixture Catalog",
        "distribution_name": "paper-reader-fixture-catalog",
        "distribution_version": "2.1.0",
        "license": "Apache-2.0",
        "protocol_minimum": "1.0",
        "protocol_maximum": "1.0",
        "compatible": True,
        "compatibility_reason": None,
        "enabled": False,
        "status": "disabled",
        "health_reason": "plugin_disabled",
        "consecutive_failures": 0,
        "circuit_open_reason": None,
        "last_called_at": None,
        "sandbox_available": True,
        "sandbox_reason": None,
        "capabilities": [
            {
                "capability_id": "fixture.catalog.score",
                "kind": "score_dimension",
                "methods": ["evaluate", "probe"],
                "timeout_seconds": 20,
                "ready": False,
                "permissions": [],
            },
            {
                "capability_id": "fixture.catalog.source",
                "kind": "source_connector",
                "methods": ["estimate", "fetch_page", "probe"],
                "timeout_seconds": 30,
                "ready": False,
                "permissions": [
                    {
                        "kind": "credential",
                        "resource": "fixture_api_key",
                        "purpose": "Authenticate through the host credential broker",
                        "granted": False,
                    },
                    {
                        "kind": "network",
                        "resource": "api.example.org",
                        "purpose": "Read public fixture metadata",
                        "granted": False,
                    },
                ],
            },
        ],
    }
    serialized = response.text.casefold()
    assert "entry_point" not in serialized
    assert "manifest_sha256" not in serialized
    assert "stderr" not in serialized
    assert "secret-value-canary" not in serialized


@pytest.mark.asyncio
async def test_permission_enable_revoke_and_reset_are_audited_and_refresh_adapters(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    refreshes: list[str] = []
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="one-time-secret",
        plugin_sandbox_launcher=FakeSandboxLauncher(),
        plugin_adapter_refresh=lambda: refreshes.append("refresh"),
    )
    registry, plugin_manifest = install(app)

    async with local_client(app) as client:
        csrf = await bootstrap(client)
        headers = mutation_headers(csrf)
        for permission in plugin_manifest.requested_permissions():
            granted = await client.post(
                "/api/v1/plugins/fixture.catalog/permissions",
                json={
                    "kind": permission.kind.value,
                    "resource": permission.resource,
                    "decision": "grant",
                },
                headers=headers,
            )
            assert granted.status_code == 200
        enabled = await client.put(
            "/api/v1/plugins/fixture.catalog/enabled",
            json={"enabled": True},
            headers=headers,
        )
        ready = await client.get("/api/v1/plugins")
        revoked = await client.post(
            "/api/v1/plugins/fixture.catalog/permissions",
            json={
                "kind": "credential",
                "resource": "fixture_api_key",
                "decision": "revoke",
            },
            headers=headers,
        )
        needs_permission = await client.get("/api/v1/plugins")

        health = PluginHealthRepository(app.state.sessions)
        for index in range(3):
            health.record_failure(
                "fixture.catalog",
                reason="protocol_error",
                called_at=datetime(2026, 7, 14, 9, index, tzinfo=UTC),
            )
        reset = await client.post(
            "/api/v1/plugins/fixture.catalog/reset",
            headers=headers,
        )

    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert ready.json()[0]["status"] == "ready"
    assert all(capability["ready"] for capability in ready.json()[0]["capabilities"])
    assert revoked.status_code == 200
    assert needs_permission.json()[0]["status"] == "permissions_required"
    source = next(
        item
        for item in needs_permission.json()[0]["capabilities"]
        if item["kind"] == "source_connector"
    )
    assert source["ready"] is False
    assert next(
        item for item in source["permissions"] if item["kind"] == "credential"
    )["granted"] is False
    assert reset.status_code == 200
    assert reset.json()["consecutive_failures"] == 0
    assert reset.json()["circuit_open_reason"] is None
    assert len(registry.permission_history("fixture.catalog")) == 3
    assert refreshes == ["refresh"] * 5


@pytest.mark.asyncio
async def test_plugin_mutations_require_csrf_and_reject_undeclared_permissions(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="one-time-secret",
        plugin_sandbox_launcher=FakeSandboxLauncher(),
    )
    install(app)

    async with local_client(app) as client:
        csrf = await bootstrap(client)
        missing_csrf = await client.put(
            "/api/v1/plugins/fixture.catalog/enabled",
            json={"enabled": True},
            headers={"Origin": "http://127.0.0.1"},
        )
        foreign_origin = await client.post(
            "/api/v1/plugins/fixture.catalog/reset",
            headers={"Origin": "https://example.org", "X-CSRF-Token": csrf},
        )
        undeclared = await client.post(
            "/api/v1/plugins/fixture.catalog/permissions",
            json={"kind": "file", "resource": "private_library", "decision": "grant"},
            headers=mutation_headers(csrf),
        )

    assert missing_csrf.status_code == 403
    assert foreign_origin.status_code == 403
    assert undeclared.status_code == 409
    assert "declared" in undeclared.json()["detail"].casefold()


@pytest.mark.asyncio
async def test_incompatible_or_sandbox_unavailable_plugins_never_report_ready(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="one-time-secret",
        plugin_sandbox_launcher=FakeSandboxLauncher(
            available=False,
            code="app_container_unavailable",
        ),
    )
    install(app, plugin_id="fixture.incompatible", compatible=False)

    async with local_client(app) as client:
        csrf = await bootstrap(client)
        enable = await client.put(
            "/api/v1/plugins/fixture.incompatible/enabled",
            json={"enabled": True},
            headers=mutation_headers(csrf),
        )
        response = await client.get("/api/v1/plugins")

    assert enable.status_code == 409
    plugin = response.json()[0]
    assert plugin["status"] == "incompatible"
    assert plugin["health_reason"] == "protocol_incompatible"
    assert plugin["sandbox_available"] is False
    assert plugin["sandbox_reason"] == "app_container_unavailable"
    assert all(capability["ready"] is False for capability in plugin["capabilities"])


def test_installation_list_is_deterministic_and_does_not_load_plugin_code(tmp_path: Path) -> None:
    database = tmp_path / "plugins.sqlite3"
    from paper_reader.migrations import upgrade_database

    upgrade_database(database)
    sessions = create_session_factory(create_sqlite_engine(database))
    registry = PluginInstallationRegistry(sessions)
    for plugin_id in ("fixture.zeta", "fixture.alpha"):
        plugin_manifest = PluginManifest.model_validate(manifest_payload(plugin_id=plugin_id))
        registry.upsert(
            DiscoveredEntryPoint(
                plugin_id=plugin_id,
                entry_point_value=f"{plugin_id.replace('.', '_')}:application",
                distribution_name="paper-reader-fixture-catalog",
                distribution_version="2.1.0",
            ),
            plugin_manifest,
            discovered_at=datetime(2026, 7, 14, tzinfo=UTC),
        )

    installations = registry.list_all()

    assert [item.plugin_id for item in installations] == ["fixture.alpha", "fixture.zeta"]


@pytest.mark.asyncio
async def test_explicit_refresh_requires_csrf_and_returns_only_stable_discovery_codes(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    class Catalog:
        calls = 0

        async def refresh(self) -> PluginCatalogRefresh:
            self.calls += 1
            return PluginCatalogRefresh(
                discovered=2,
                registered=1,
                issues=(
                    PluginCatalogIssue(
                        plugin_id="fixture.broken",
                        code="manifest_worker_failed",
                    ),
                ),
            )

    catalog = Catalog()
    refreshes: list[str] = []
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="one-time-secret",
        plugin_sandbox_launcher=FakeSandboxLauncher(),
        plugin_adapter_refresh=lambda: refreshes.append("refresh"),
    )
    app.state.plugin_catalog = catalog

    async with local_client(app) as client:
        csrf = await bootstrap(client)
        rejected = await client.post(
            "/api/v1/plugins/refresh",
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.post(
            "/api/v1/plugins/refresh",
            headers=mutation_headers(csrf),
        )

    assert rejected.status_code == 403
    assert response.status_code == 200
    assert response.json() == {
        "discovered": 2,
        "registered": 1,
        "issues": [
            {"plugin_id": "fixture.broken", "code": "manifest_worker_failed"}
        ],
    }
    assert "private" not in response.text.casefold()
    assert catalog.calls == 1
    assert refreshes == ["refresh"]
