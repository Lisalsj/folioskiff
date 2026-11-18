from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from paper_reader.agents.contracts import (
    AgentCapability,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    ModelCatalog,
)
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.settings import AppPaths


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeAgentBackend:
    def __init__(
        self,
        *,
        backend_id: str,
        location: AgentLocation,
        available: bool,
        models: tuple[AgentModel, ...],
    ) -> None:
        self.capability = AgentCapability(
            backend_id=backend_id,
            display_name=backend_id.replace("_", " ").title(),
            location=location,
            probe_code=(
                AgentProbeCode.READY
                if available
                else AgentProbeCode.NOT_AUTHENTICATED
            ),
            installed=True,
            authenticated=available,
            available=available,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=backend_id != "ollama_cloud",
            supports_external_research=backend_id == "codex_cli",
            degradation_reasons=() if available else ("missing_credential",),
        )
        self._models = models
        self.discovery_calls = 0
        self.generation_calls = 0

    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=self._models,
            supports_manual_model_id=True,
            supports_default_model=self.capability.backend_id == "codex_cli",
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.generation_calls += 1
        raise AssertionError("Capability settings must not invoke a model")


def _registry() -> AgentBackendRegistry:
    return AgentBackendRegistry(
        (
            FakeAgentBackend(
                backend_id="codex_cli",
                location=AgentLocation.CLOUD,
                available=True,
                models=(),
            ),
            FakeAgentBackend(
                backend_id="ollama_local",
                location=AgentLocation.LOCAL,
                available=True,
                models=(AgentModel(model_id="local-model", display_name="Local model"),),
            ),
            FakeAgentBackend(
                backend_id="ollama_cloud",
                location=AgentLocation.CLOUD,
                available=False,
                models=(),
            ),
        )
    )


def test_builtin_agent_refresh_preserves_registry_identity_and_plugin_extensions() -> None:
    existing = _registry()
    plugin = FakeAgentBackend(
        backend_id="fixture.agent",
        location=AgentLocation.LOCAL,
        available=True,
        models=(),
    )
    existing.replace_extensions((plugin,))
    before_identity = id(existing)
    replacement = AgentBackendRegistry(
        (
            FakeAgentBackend(
                backend_id="codex_cli",
                location=AgentLocation.CLOUD,
                available=False,
                models=(),
            ),
        )
    )
    refreshes: list[str] = []
    state = SimpleNamespace(
        agent_registry=existing,
        agent_registry_factory=lambda key: replacement,
        credential_store=MemoryCredentialStore(),
        plugin_adapter_refresh=lambda: refreshes.append("plugins"),
    )

    from paper_reader.settings_api import _refresh_agent_registry

    _refresh_agent_registry(SimpleNamespace(app=SimpleNamespace(state=state)))  # type: ignore[arg-type]

    assert id(state.agent_registry) == before_identity
    assert state.agent_registry.get("codex_cli") is replacement.get("codex_cli")
    assert state.agent_registry.get("fixture.agent") is plugin
    assert state.agent_registry.get("ollama_local") is None
    assert refreshes == ["plugins"]


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


@pytest.mark.asyncio
async def test_agent_capabilities_list_models_without_invoking_generation(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    registry = _registry()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=registry,
    )

    async with local_client(app) as client:
        unauthorized = await client.get("/api/v1/settings/agents")
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.get("/api/v1/settings/agents")

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert [item["backend_id"] for item in payload] == [
        "codex_cli",
        "ollama_cloud",
        "ollama_local",
    ]
    local = next(item for item in payload if item["backend_id"] == "ollama_local")
    assert local["location"] == "local"
    assert local["probe_code"] == "ready"
    assert local["recommended_action"] is None
    assert local["models"] == [
        {
            "model_id": "local-model",
            "display_name": "Local model",
            "supports_vision": None,
            "context_window": None,
        }
    ]
    cloud = next(item for item in payload if item["backend_id"] == "ollama_cloud")
    assert cloud["credential_required"] is True
    assert cloud["credential_configured"] is False
    assert cloud["available"] is False
    assert cloud["probe_code"] == "not_authenticated"
    assert cloud["recommended_action"]
    for backend in registry.all():
        assert isinstance(backend, FakeAgentBackend)
        assert backend.generation_calls == 0


@pytest.mark.asyncio
async def test_ollama_cloud_key_uses_vault_never_api_or_database_and_can_be_removed(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.credentials import OLLAMA_CLOUD_CREDENTIAL

    store = MemoryCredentialStore()
    paths = _paths(tmp_path)
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        credential_store=store,
        agent_registry=_registry(),
    )
    secret = "ollama-cloud-do-not-persist-123"

    async with local_client(app) as client:
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
        missing_csrf = await client.put(
            "/api/v1/settings/agents/ollama-cloud/credential",
            json={"value": secret},
            headers={"Origin": "http://127.0.0.1"},
        )
        saved = await client.put(
            "/api/v1/settings/agents/ollama-cloud/credential",
            json={"value": secret},
            headers=headers,
        )
        capabilities = await client.get("/api/v1/settings/agents")
        removed = await client.delete(
            "/api/v1/settings/agents/ollama-cloud/credential",
            headers=headers,
        )

    assert missing_csrf.status_code == 403
    assert saved.status_code == 200
    assert saved.json() == {"backend_id": "ollama_cloud", "configured": True}
    assert secret not in saved.text
    cloud = next(
        item for item in capabilities.json() if item["backend_id"] == "ollama_cloud"
    )
    assert cloud["credential_configured"] is True
    assert removed.json() == {"backend_id": "ollama_cloud", "configured": False}
    assert store.get(OLLAMA_CLOUD_CREDENTIAL) is None
    for database_file in paths.state_root.glob("paper-reader.sqlite3*"):
        assert secret.encode() not in database_file.read_bytes()


@pytest.mark.asyncio
async def test_unavailable_credential_store_returns_503(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=_registry(),
    )
    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.put(
            "/api/v1/settings/agents/ollama-cloud/credential",
            json={"value": "secret"},
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": client.cookies["paper_reader_csrf"],
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"
