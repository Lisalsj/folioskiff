"""Immutable contracts for the guided first-run workflow."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from paper_reader.agents.contracts import AgentProbeCode


class OnboardingStep(StrEnum):
    STORAGE_PRIVACY = "storage_privacy"
    AGENTS = "agents"
    SOURCES = "sources"
    READING_PREFERENCES = "reading_preferences"
    COMPLETE = "complete"


class OnboardingStepStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class OnboardingStepUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: OnboardingStep
    status: OnboardingStepStatus


class OnboardingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_step: OnboardingStep
    completed: bool
    steps: dict[OnboardingStep, OnboardingStepStatus]
    privacy_acknowledged_at: datetime | None
    updated_at: datetime


class StorageCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    writable: bool
    state_writable: bool
    cache_writable: bool
    library_writable: bool
    state_root: Path
    cache_root: Path
    library_root: Path
    backups_root: Path
    data_root: Path | None = Field(default=None, exclude_if=lambda value: value is None)


class RuntimePathUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_root: Path | None = None
    # Accepted during the 3.0 -> 3.1 transition; new clients send data_root.
    cache_root: Path | None = None
    library_root: Path | None = None

    @model_validator(mode="after")
    def validate_layout(self) -> RuntimePathUpdate:
        if self.data_root is not None and (
            self.cache_root is not None or self.library_root is not None
        ):
            raise ValueError("data_root cannot be combined with legacy roots")
        if self.data_root is None and (self.cache_root is None or self.library_root is None):
            raise ValueError("data_root is required")
        return self


class RuntimePathSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_root: Path
    cache_root: Path
    library_root: Path
    backups_root: Path
    data_root: Path | None = Field(default=None, exclude_if=lambda value: value is None)
    restart_required: bool


class RuntimeRestartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    restart_required: Literal[True] = True


class AgentSetupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    display_name: str
    supports_vision: bool | None
    context_window: int | None


class AgentSetupCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend_id: str
    display_name: str
    location: str
    version: str | None = None
    probe_code: AgentProbeCode
    recommended_action: str | None
    installed: bool
    authenticated: bool
    available: bool
    supports_manual_model_id: bool
    supports_default_model: bool
    models: tuple[AgentSetupModel, ...]
    degradation_reasons: tuple[str, ...]
    desktop_app_running: bool | None = None


class SourceSetupCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str
    display_name: str
    enabled: bool
    credential_requirement: str
    credential_configured: bool
    disabled_reason: str | None
    setup_url: str | None


class RetrievalSetupCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lexical_enabled: bool
    semantic_enabled: bool
    model_state: str
    degradation_reasons: tuple[str, ...]


class OnboardingOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: OnboardingSnapshot
    storage: StorageCapability
    agents: tuple[AgentSetupCapability, ...]
    sources: tuple[SourceSetupCapability, ...]
    retrieval: RetrievalSetupCapability
    blocking_issues: tuple[str, ...]


ONBOARDING_STEPS: tuple[OnboardingStep, ...] = (
    OnboardingStep.STORAGE_PRIVACY,
    OnboardingStep.AGENTS,
    OnboardingStep.SOURCES,
    OnboardingStep.READING_PREFERENCES,
)


__all__ = [
    "ONBOARDING_STEPS",
    "AgentSetupCapability",
    "AgentSetupModel",
    "OnboardingOverview",
    "OnboardingSnapshot",
    "OnboardingStep",
    "OnboardingStepStatus",
    "OnboardingStepUpdate",
    "RetrievalSetupCapability",
    "RuntimePathSnapshot",
    "RuntimePathUpdate",
    "RuntimeRestartResponse",
    "SourceSetupCapability",
    "StorageCapability",
]
