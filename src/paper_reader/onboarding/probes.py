"""Side-effect-limited capability aggregation for guided setup."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from secrets import token_hex

from paper_reader.agents.contracts import AgentBackendError, AgentModel, ModelCatalog
from paper_reader.agents.diagnostics import agent_recommended_action
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.discovery.connectors import ConnectorRegistry
from paper_reader.onboarding.contracts import (
    AgentSetupCapability,
    AgentSetupModel,
    OnboardingOverview,
    OnboardingSnapshot,
    RetrievalSetupCapability,
    SourceSetupCapability,
    StorageCapability,
)
from paper_reader.recommendation.semantic import VectorCapability
from paper_reader.settings import AppPaths

StorageChecker = Callable[[Path], bool]


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if checker is not None else False
    except OSError:
        return True


def _storage_writable(root: Path) -> bool:
    candidate = root / f".paper-reader-write-probe-{token_hex(8)}"
    descriptor: int | None = None
    try:
        if not root.is_dir() or _is_link_or_junction(root):
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        descriptor = os.open(candidate, flags, 0o600)
        os.set_inheritable(descriptor, False)
        os.write(descriptor, b"paper-reader storage probe\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        candidate.unlink()
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            candidate.unlink(missing_ok=True)


def _setup_models(models: tuple[AgentModel, ...]) -> tuple[AgentSetupModel, ...]:
    return tuple(
        AgentSetupModel(
            model_id=model.model_id,
            display_name=model.display_name,
            supports_vision=model.supports_vision,
            context_window=model.context_window,
        )
        for model in models
    )


class OnboardingCapabilityProbe:
    """Inspect setup capabilities without fetching papers or generating text."""

    def __init__(self, *, storage_checker: StorageChecker = _storage_writable) -> None:
        self._storage_checker = storage_checker

    async def overview(
        self,
        *,
        state: OnboardingSnapshot,
        paths: AppPaths,
        agents: AgentBackendRegistry,
        sources: ConnectorRegistry,
        retrieval: VectorCapability,
        refresh_agents: bool,
    ) -> OnboardingOverview:
        storage = self._storage(paths)
        agent_capabilities = await self._agents(agents, refresh=refresh_agents)
        source_capabilities = tuple(
            SourceSetupCapability(
                connector_id=item.capabilities.connector_id,
                display_name=item.capabilities.display_name,
                enabled=item.capabilities.enabled,
                credential_requirement=item.capabilities.credential_requirement,
                credential_configured=item.capabilities.credential_configured,
                disabled_reason=item.capabilities.disabled_reason,
                setup_url=item.capabilities.setup_url,
            )
            for item in sorted(
                sources.all(),
                key=lambda connector: connector.capabilities.connector_id,
            )
        )
        retrieval_capability = RetrievalSetupCapability(
            lexical_enabled=True,
            semantic_enabled=retrieval.enabled,
            model_state=retrieval.model_state,
            degradation_reasons=retrieval.degradation_reasons,
        )
        return OnboardingOverview(
            state=state,
            storage=storage,
            agents=agent_capabilities,
            sources=source_capabilities,
            retrieval=retrieval_capability,
            blocking_issues=(() if storage.writable else ("storage_unwritable",)),
        )

    def _storage(self, paths: AppPaths) -> StorageCapability:
        state_writable = self._storage_checker(paths.state_root)
        cache_writable = self._storage_checker(paths.cache_root)
        library_writable = self._storage_checker(paths.library_root)
        return StorageCapability(
            writable=state_writable and cache_writable and library_writable,
            state_writable=state_writable,
            cache_writable=cache_writable,
            library_writable=library_writable,
            state_root=paths.state_root,
            cache_root=paths.cache_root,
            library_root=paths.library_root,
            backups_root=paths.backups,
            data_root=paths.data_root,
        )

    async def _agents(
        self,
        registry: AgentBackendRegistry,
        *,
        refresh: bool,
    ) -> tuple[AgentSetupCapability, ...]:
        responses: list[AgentSetupCapability] = []
        for backend in registry.all():
            previous_catalog = getattr(backend, "cached_model_catalog", None)
            if refresh:
                probe = getattr(backend, "probe", None)
                if callable(probe):
                    with suppress(AgentBackendError):
                        await probe()
            capability = backend.capability
            cached = getattr(backend, "cached_model_catalog", None)
            catalog = cached if isinstance(cached, ModelCatalog) else None
            catalog_reason: tuple[str, ...] = ()
            if refresh and (catalog is None or catalog is previous_catalog):
                try:
                    catalog = await backend.discover_models()
                except AgentBackendError as error:
                    catalog_reason = (f"model_discovery_{error.code.value}",)
            if catalog is None:
                catalog = ModelCatalog()
            reasons = tuple(
                dict.fromkeys(
                    (
                        *capability.degradation_reasons,
                        *catalog.degradation_reasons,
                        *catalog_reason,
                    )
                )
            )
            responses.append(
                AgentSetupCapability(
                    backend_id=capability.backend_id,
                    display_name=capability.display_name,
                    location=capability.location.value,
                    version=self._safe_version(getattr(backend, "cli_version", None)),
                    probe_code=capability.probe_code,
                    recommended_action=agent_recommended_action(capability.probe_code),
                    installed=capability.installed,
                    authenticated=capability.authenticated,
                    available=capability.available,
                    supports_manual_model_id=catalog.supports_manual_model_id,
                    supports_default_model=catalog.supports_default_model,
                    models=_setup_models(catalog.models),
                    degradation_reasons=reasons,
                    desktop_app_running=capability.desktop_app_running,
                )
            )
        return tuple(responses)

    @staticmethod
    def _safe_version(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 80 or not cleaned.isprintable():
            return None
        return cleaned


__all__ = ["OnboardingCapabilityProbe", "StorageChecker"]
