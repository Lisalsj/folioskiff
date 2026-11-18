"""Shared immutable Agent preflight contracts and readiness errors."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from paper_reader.agents.contracts import (
    AgentLocation,
    ModelCatalog,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.evidence import canonical_json
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.agents.reports import ReportLanguage


class AnalysisPreflightConflict(RuntimeError):
    pass


class AnalysisNotReadyError(RuntimeError):
    pass


class ComparisonNotReadyError(RuntimeError):
    pass


class ComparisonSelectionConflict(RuntimeError):
    pass


async def discover_model_catalog(
    backends: AgentBackendRegistry,
    backend_id: str,
) -> tuple[ModelCatalog, str | None]:
    """Discover models without turning a transient probe failure into a crash."""

    try:
        return await backends.discover_models(backend_id), None
    except Exception:
        return ModelCatalog(), "model_discovery_failed"


def model_selection_status(
    catalog: ModelCatalog,
    model_id: str | None,
) -> tuple[bool, tuple[str, ...]]:
    """Evaluate a selected model against one immutable discovered catalog."""

    model_ids = {item.model_id for item in catalog.models}
    if model_id is None and not catalog.supports_default_model:
        return False, ("model_required",)
    if model_id is not None and model_id not in model_ids:
        if catalog.supports_manual_model_id:
            return True, ("manual_model_id",)
        return False, ("model_unavailable",)
    return True, ()


def comparison_request_fingerprint(
    *,
    comparison_key_value: str,
    ordered_items: Sequence[tuple[str, str | None, str | None]],
    mode: str,
    backend_id: str,
    model_id: str | None,
    reasoning_effort: str,
    service_tier: str,
    language: str,
    prompt_sha256: str,
) -> str:
    """Hash the exact ordered evidence and execution choices from preflight."""

    return sha256(
        canonical_json(
            {
                "comparison_key": comparison_key_value,
                "ordered_items": [
                    {
                        "work_id": work_id,
                        "document_version_id": document_version_id,
                        "evidence_sha256": evidence_sha256,
                    }
                    for work_id, document_version_id, evidence_sha256 in ordered_items
                ],
                "mode": mode,
                "backend_id": backend_id,
                "model_id": model_id,
                "reasoning_effort": reasoning_effort,
                "service_tier": service_tier,
                "language": language,
                "prompt_sha256": prompt_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()


class PreflightModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    model_id: str
    display_name: str
    supports_vision: bool | None
    context_window: int | None


class ComparisonPreflightItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int = Field(ge=0)
    requested_work_id: str
    work_id: str
    title: str
    document_version_id: str | None
    document_content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    evidence_level: Literal["full_text", "abstract_only"] | None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    coverage: int = Field(ge=0, le=100)
    status: Literal["available", "unavailable"]
    warnings: tuple[str, ...]


class ComparisonPreflight(BaseModel):
    model_config = ConfigDict(frozen=True)

    comparison_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["direct", "staged"]
    items: tuple[ComparisonPreflightItem, ...]
    item_count: int = Field(ge=2)
    available_count: int = Field(ge=0)
    coverage: int = Field(ge=0, le=100)
    backend_id: str
    backend_display_name: str
    backend_location: AgentLocation
    cloud_backend: bool
    model_id: str | None
    model_options: tuple[PreflightModel, ...]
    manual_model_id_allowed: bool
    default_model_allowed: bool
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    language: Literal["zh-CN", "en", "bilingual"]
    ready: bool
    warnings: tuple[str, ...]
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisPreflight(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_id: str = Field(min_length=1, max_length=80)
    document_version_id: str | None = Field(default=None, max_length=80)
    document_content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    evidence_level: Literal["full_text", "abstract_only"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: int = Field(ge=0, le=100)
    backend_id: str = Field(min_length=1, max_length=80)
    backend_display_name: str
    backend_location: AgentLocation
    cloud_backend: bool
    model_id: str | None
    model_options: tuple[PreflightModel, ...]
    manual_model_id_allowed: bool
    default_model_allowed: bool
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    language: ReportLanguage
    template_version_id: str = Field(min_length=1, max_length=80)
    template_name: str
    external_research: bool
    scope: tuple[str, ...]
    warnings: tuple[str, ...]
    ready: bool
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
