"""Application orchestration for onboarding state and capability snapshots."""

from __future__ import annotations

from pathlib import Path

from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.discovery.connectors import ConnectorRegistry
from paper_reader.onboarding.contracts import (
    OnboardingOverview,
    OnboardingSnapshot,
    OnboardingStep,
    OnboardingStepStatus,
    OnboardingStepUpdate,
    RuntimePathSnapshot,
    RuntimePathUpdate,
)
from paper_reader.onboarding.probes import OnboardingCapabilityProbe
from paper_reader.onboarding.repository import OnboardingRepository
from paper_reader.onboarding.reset import DataResetService, PendingDataReset
from paper_reader.recommendation.semantic import VectorCapability
from paper_reader.runtime_config import RuntimeConfigStore, RuntimeConfigUpdate
from paper_reader.runtime_control import RuntimeControl
from paper_reader.runtime_migration import validate_data_root_change
from paper_reader.settings import AppPaths


class OnboardingService:
    def __init__(
        self,
        *,
        repository: OnboardingRepository,
        paths: AppPaths,
        runtime_control: RuntimeControl,
        capability_probe: OnboardingCapabilityProbe | None = None,
    ) -> None:
        self._repository = repository
        self._paths = paths
        self._runtime_control = runtime_control
        self._capability_probe = capability_probe or OnboardingCapabilityProbe()
        self._data_reset = DataResetService(paths)
        self._runtime_config = RuntimeConfigStore(paths.state_root)

    async def overview(
        self,
        *,
        agents: AgentBackendRegistry,
        sources: ConnectorRegistry,
        retrieval: VectorCapability,
        refresh_agents: bool = False,
    ) -> OnboardingOverview:
        return await self._capability_probe.overview(
            state=self._repository.snapshot(),
            paths=self._paths,
            agents=agents,
            sources=sources,
            retrieval=retrieval,
            refresh_agents=refresh_agents,
        )

    def save_step(
        self,
        step: OnboardingStep,
        status: OnboardingStepStatus,
    ) -> OnboardingSnapshot:
        return self._repository.save_step(OnboardingStepUpdate(step=step, status=status))

    def stage_data_reset(self, confirmation: str) -> PendingDataReset:
        return self._data_reset.stage(confirmation)

    def save_runtime_paths(self, update: RuntimePathUpdate) -> RuntimePathSnapshot:
        data_root: Path | None
        state_root = self._paths.state_root
        cache_root: Path
        library_root: Path
        if update.data_root is not None:
            validate_data_root_change(self._paths, update.data_root)
            saved = self._runtime_config.save(
                RuntimeConfigUpdate(
                    data_root=update.data_root,
                    legacy_cache_root=self._paths.cache_root,
                    legacy_library_root=self._paths.library_root,
                )
            )
            from paper_reader.settings import AppPaths

            next_paths = AppPaths.from_data_root(saved.data_root or update.data_root)
            data_root = next_paths.data_root or next_paths.library_root.parent
            state_root = next_paths.state_root
            cache_root = next_paths.cache_root
            library_root = next_paths.library_root
        else:
            saved = self._runtime_config.save(
                RuntimeConfigUpdate(
                    cache_root=update.cache_root,
                    library_root=update.library_root,
                )
            )
            # Keep the legacy response shape when an older client submits
            # separate cache/library roots.  New clients use data_root above.
            data_root = self._paths.data_root
            cache_root = saved.cache_root or self._paths.cache_root
            library_root = saved.library_root or self._paths.library_root
        return RuntimePathSnapshot(
            state_root=state_root,
            cache_root=cache_root,
            library_root=library_root,
            backups_root=library_root / "backups",
            data_root=data_root,
            restart_required=(
                cache_root != self._paths.cache_root
                or library_root != self._paths.library_root
                or data_root != self._paths.data_root
            ),
        )

    def request_data_reset_restart(self) -> None:
        self._runtime_control.request_restart("onboarding_data_reset")

    def request_runtime_config_restart(self) -> None:
        self._runtime_control.request_restart("runtime_config_updated")


__all__ = ["OnboardingService"]
