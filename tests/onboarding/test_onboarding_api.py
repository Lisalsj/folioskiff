from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
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
from paper_reader.discovery.connectors import (
    ConnectorCapabilities,
    ConnectorEstimate,
    ConnectorRegistry,
    SourcePage,
    SourceQuery,
)
from paper_reader.onboarding.probes import OnboardingCapabilityProbe, _storage_writable
from paper_reader.runtime_config import RuntimeConfigStore
from paper_reader.runtime_control import RuntimeControl
from paper_reader.settings import AppPaths


class ProbeOnlyAgent:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Codex CLI",
            location=AgentLocation.CLOUD,
            probe_code=AgentProbeCode.NOT_INSTALLED,
            installed=False,
            authenticated=False,
            available=False,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=True,
            supports_external_research=True,
            degradation_reasons=("codex_cli_not_installed",),
        )
        self.probe_calls = 0
        self.discovery_calls = 0
        self.generation_calls = 0
        self.cli_version = "0.101.0"
        self.cached_model_catalog = ModelCatalog(
            models=(AgentModel(model_id="stale-model", display_name="Stale model"),),
            supports_manual_model_id=True,
            supports_default_model=True,
        )

    async def probe(self) -> AgentCapability:
        self.probe_calls += 1
        return self.capability

    async def discover_models(self) -> ModelCatalog:
        self.discovery_calls += 1
        return ModelCatalog(
            models=(AgentModel(model_id="manual-model", display_name="Manual model"),),
            supports_manual_model_id=True,
            supports_default_model=True,
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.generation_calls += 1
        raise AssertionError("onboarding capability detection must not generate")


class CapabilityOnlySource:
    capabilities = ConnectorCapabilities(
        connector_id="arxiv",
        display_name="arXiv",
        credential_requirement="none",
        supports_exact_updated_date=True,
        enabled=True,
        credential_configured=False,
        setup_url=None,
    )

    def __init__(self) -> None:
        self.query_calls = 0

    async def estimate(self, query: SourceQuery) -> ConnectorEstimate:
        self.query_calls += 1
        raise AssertionError("onboarding capability detection must not estimate papers")

    async def fetch_page(
        self,
        query: SourceQuery,
        *,
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        self.query_calls += 1
        raise AssertionError("onboarding capability detection must not fetch papers")


@asynccontextmanager
async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client,
    ):
        yield client


async def _bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


def test_storage_probe_cleanup_failure_degrades_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    root.mkdir()
    original_unlink = Path.unlink

    def guarded_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.startswith(".paper-reader-write-probe-"):
            raise PermissionError("probe cleanup denied")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)

    assert _storage_writable(root) is False


@pytest.mark.asyncio
async def test_onboarding_snapshot_is_usable_without_agents_or_keys(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    agent = ProbeOnlyAgent()
    source = CapabilityOnlySource()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((agent,)),
        connector_registry=ConnectorRegistry((source,)),
    )

    async with _client(app) as client:
        unauthorized = await client.get("/api/v1/onboarding")
        await _bootstrap(client)
        response = await client.get("/api/v1/onboarding")

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["current_step"] == "storage_privacy"
    assert payload["storage"]["writable"] is True
    assert payload["agents"][0]["probe_code"] == "not_installed"
    assert payload["agents"][0]["version"] == "0.101.0"
    assert payload["agents"][0]["recommended_action"]
    assert payload["sources"] == [
        {
            "connector_id": "arxiv",
            "display_name": "arXiv",
            "enabled": True,
            "credential_requirement": "none",
            "credential_configured": False,
            "disabled_reason": None,
            "setup_url": None,
        }
    ]
    assert payload["blocking_issues"] == []
    assert agent.probe_calls == 0
    assert agent.discovery_calls == 0
    assert agent.generation_calls == 0
    assert source.query_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [False, True])
async def test_ollama_probe_discovers_models_once_per_refresh(offline: bool) -> None:
    from paper_reader.agents.ollama import OllamaBackend
    from paper_reader.onboarding.probes import OnboardingCapabilityProbe

    paths: list[str] = []

    def tags(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if offline:
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(200, json={"models": [{"name": f"model-{len(paths)}"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(tags)) as client:
        agents = AgentBackendRegistry((OllamaBackend.local(client=client),))
        probe = OnboardingCapabilityProbe()
        await probe._agents(agents, refresh=False)
        assert paths == []
        for refresh in (1, 2):
            (capability,) = await probe._agents(agents, refresh=True)
            assert paths == ["/api/tags"] * refresh
            assert capability.available is not offline
            if offline:
                assert capability.probe_code == "probe_timeout"
            else:
                assert [model.model_id for model in capability.models] == [f"model-{refresh}"]


@pytest.mark.asyncio
async def test_explicit_probe_never_generates_or_queries_papers(tmp_path: Path) -> None:
    from paper_reader.app import create_app

    agent = ProbeOnlyAgent()
    source = CapabilityOnlySource()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((agent,)),
        connector_registry=ConnectorRegistry((source,)),
    )

    async with _client(app) as client:
        headers = await _bootstrap(client)
        missing_csrf = await client.post(
            "/api/v1/onboarding/probe",
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.post("/api/v1/onboarding/probe", headers=headers)

    assert missing_csrf.status_code == 403
    assert response.status_code == 200
    assert response.json()["agents"][0]["models"][0]["model_id"] == "manual-model"
    assert agent.probe_calls == 1
    assert agent.discovery_calls == 1
    assert agent.generation_calls == 0
    assert source.query_calls == 0


@pytest.mark.asyncio
async def test_onboarding_step_and_data_reset_require_safe_mutation(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    runtime = RuntimeControl()
    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((ProbeOnlyAgent(),)),
        connector_registry=ConnectorRegistry((CapabilityOnlySource(),)),
        runtime_control=runtime,
    )

    async with _client(app) as client:
        headers = await _bootstrap(client)
        missing_origin = await client.put(
            "/api/v1/onboarding/steps/storage_privacy",
            json={"status": "completed"},
            headers={"X-CSRF-Token": headers["X-CSRF-Token"]},
        )
        storage = await client.put(
            "/api/v1/onboarding/steps/storage_privacy",
            json={"status": "completed"},
            headers=headers,
        )
        wrong_confirmation = await client.post(
            "/api/v1/onboarding/data-reset",
            json={"confirmation": "yes"},
            headers=headers,
        )
        reset = await client.post(
            "/api/v1/onboarding/data-reset",
            json={"confirmation": "REBUILD PAPER-READER DATA"},
            headers=headers,
        )

    assert missing_origin.status_code == 403
    assert storage.status_code == 200
    assert storage.json()["current_step"] == "agents"
    assert wrong_confirmation.status_code == 422
    assert reset.status_code == 202
    assert reset.json()["restart_required"] is True
    assert "database" not in reset.text.casefold()
    assert runtime.restart_requested is True
    assert runtime.restart_reason == "onboarding_data_reset"


@pytest.mark.asyncio
async def test_storage_is_the_only_blocking_onboarding_capability(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((ProbeOnlyAgent(),)),
        connector_registry=ConnectorRegistry((CapabilityOnlySource(),)),
        onboarding_capability_probe=OnboardingCapabilityProbe(
            storage_checker=lambda _: False,
        ),
    )

    async with _client(app) as client:
        await _bootstrap(client)
        response = await client.get("/api/v1/onboarding")

    assert response.status_code == 200
    payload = response.json()
    assert payload["storage"]["writable"] is False
    assert payload["blocking_issues"] == ["storage_unwritable"]
    assert payload["agents"][0]["probe_code"] == "not_installed"
    assert payload["sources"][0]["enabled"] is True


@pytest.mark.asyncio
async def test_runtime_paths_are_staged_before_an_explicit_controlled_restart(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    paths = _paths(tmp_path)
    runtime = RuntimeControl()
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((ProbeOnlyAgent(),)),
        connector_registry=ConnectorRegistry((CapabilityOnlySource(),)),
        runtime_control=runtime,
    )
    next_cache = tmp_path / "next-cache"
    next_library = tmp_path / "next-library"

    async with _client(app) as client:
        headers = await _bootstrap(client)
        overview = await client.get("/api/v1/onboarding")
        rejected = await client.put(
            "/api/v1/onboarding/runtime-config",
            json={"cache_root": str(next_cache), "library_root": str(next_library)},
            headers={"Origin": "http://127.0.0.1"},
        )
        invalid = await client.put(
            "/api/v1/onboarding/runtime-config",
            json={"cache_root": "cache", "library_root": "papers"},
            headers=headers,
        )
        staged = await client.put(
            "/api/v1/onboarding/runtime-config",
            json={"cache_root": str(next_cache), "library_root": str(next_library)},
            headers=headers,
        )
        restart = await client.post(
            "/api/v1/onboarding/runtime-config/restart",
            headers=headers,
        )

    assert overview.status_code == 200
    assert overview.json()["storage"] == {
        "writable": True,
        "state_writable": True,
        "cache_writable": True,
        "library_writable": True,
        "state_root": str(paths.state_root),
        "cache_root": str(paths.cache_root),
        "library_root": str(paths.library_root),
        "backups_root": str(paths.backups),
    }
    assert rejected.status_code == 403
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": {"code": "runtime_config_invalid"}}
    assert staged.status_code == 200
    assert staged.json() == {
        "state_root": str(paths.state_root),
        "cache_root": str(next_cache),
        "library_root": str(next_library),
        "backups_root": str(next_library / "backups"),
        "restart_required": True,
    }
    saved = RuntimeConfigStore(paths.state_root).load()
    assert saved.cache_root == next_cache
    assert saved.library_root == next_library
    assert restart.status_code == 202
    assert restart.json()["restart_required"] is True
    assert runtime.restart_requested is True
    assert runtime.restart_reason == "runtime_config_updated"


@pytest.mark.asyncio
async def test_data_root_response_contains_all_derived_paths(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    paths = _paths(tmp_path)
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((ProbeOnlyAgent(),)),
        connector_registry=ConnectorRegistry((CapabilityOnlySource(),)),
    )
    data_root = tmp_path / "selected-root"

    async with _client(app) as client:
        headers = await _bootstrap(client)
        response = await client.put(
            "/api/v1/onboarding/runtime-config",
            json={"data_root": str(data_root)},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == {
        "state_root": str(data_root / "state"),
        "cache_root": str(data_root / "cache"),
        "library_root": str(data_root / "library"),
        "backups_root": str(data_root / "library" / "backups"),
        "data_root": str(data_root),
        "restart_required": True,
    }
