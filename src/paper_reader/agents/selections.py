"""Validated selections and durable Agent job commands."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.agents.reports import ReportLanguage


class ComparisonItemSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    work_id: str = Field(min_length=1, max_length=80)
    document_version_id: str | None = Field(default=None, max_length=80)

    @field_validator("work_id", "document_version_id")
    @classmethod
    def _safe_identifier(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("identifier cannot contain control characters")
        return value


class ComparisonSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    items: tuple[ComparisonItemSelection, ...] = Field(min_length=2, max_length=50)
    backend_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=200)
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None
    language: Literal["zh-CN", "en", "bilingual"] | None = None
    user_instruction: str | None = Field(default=None, max_length=20_000)

    @field_validator("backend_id", "model_id")
    @classmethod
    def _optional_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("identifier cannot be blank")
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("identifier cannot contain control characters")
        return value

    @field_validator("user_instruction")
    @classmethod
    def _optional_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ComparisonJobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection: ComparisonSelection
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalysisSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    document_version_id: str | None = Field(default=None, max_length=80)
    backend_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=200)
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None
    language: ReportLanguage | None = None
    template_version_id: str | None = Field(default=None, max_length=80)
    external_research: bool | None = None
    include_visual_evidence: bool = True
    research_intent: str | None = Field(default=None, max_length=20_000)
    temporary_instruction: str | None = Field(default=None, max_length=20_000)

    @field_validator(
        "document_version_id",
        "backend_id",
        "model_id",
        "template_version_id",
    )
    @classmethod
    def _optional_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("identifier cannot be blank")
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("identifier cannot contain control characters")
        return value


class AnalysisJobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection: AnalysisSelection
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
