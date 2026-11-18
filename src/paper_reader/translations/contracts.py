"""Closed public contracts for evidence-bound paper translation."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from paper_reader.assistant.contracts import (
    AgentSelection,
    ContextScope,
    ContextSelection,
    NetworkPolicy,
)

SUPPORTED_LANGUAGES = frozenset({"en", "zh-CN", "zh-TW", "ja", "ko", "de", "fr"})
TranslationLanguage = Literal["en", "zh-CN", "zh-TW", "ja", "ko", "de", "fr"]
TranslationEvidenceLevel = Literal["native_text", "ocr_text", "mixed_text"]

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class TranslationScope(StrEnum):
    SELECTION = "selection"
    PAGE = "page"
    SECTION = "section"
    FULL_DOCUMENT = "full_document"


class TranslationStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def _bounded_text(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} cannot contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return normalized


def normalize_source_term(value: str) -> str:
    """Return the stable paper-level identity used for terminology conflicts."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class TranslationTermInput(_StrictModel):
    source_term: str = Field(min_length=1, max_length=500)
    target_term: str = Field(min_length=1, max_length=1_000)
    locked: bool = True

    @field_validator("source_term", "target_term", mode="before")
    @classmethod
    def _safe_terms(cls, value: Any, info: Any) -> str:
        maximum = 500 if info.field_name == "source_term" else 1_000
        return _bounded_text(value, name=info.field_name, maximum=maximum)


class TranslationCommand(_StrictModel):
    context: ContextSelection
    source_language: TranslationLanguage = "en"
    target_language: TranslationLanguage = "zh-CN"
    agent: AgentSelection = Field(default_factory=lambda: AgentSelection(backend_id="codex_cli"))
    user_terminology: tuple[TranslationTermInput, ...] = Field(
        default=(),
        max_length=500,
    )
    network_policy: NetworkPolicy = NetworkPolicy.LOCAL_ONLY

    @field_validator("source_language", "target_language", mode="before")
    @classmethod
    def _supported_language(cls, value: Any) -> str:
        if not isinstance(value, str) or value not in SUPPORTED_LANGUAGES:
            raise ValueError("language must be one of the supported language tags")
        return value

    @model_validator(mode="after")
    def _safe_translation(self) -> TranslationCommand:
        if self.source_language == self.target_language:
            raise ValueError("source and target languages must be different")
        if self.network_policy is not NetworkPolicy.LOCAL_ONLY:
            raise ValueError("translation requires network_policy=local_only")
        if self.context.scope not in _CONTEXT_TO_TRANSLATION_SCOPE:
            raise ValueError("context scope is not supported for translation")
        normalized_terms = tuple(
            normalize_source_term(term.source_term) for term in self.user_terminology
        )
        if len(normalized_terms) != len(set(normalized_terms)):
            raise ValueError("duplicate user terminology source terms are not allowed")
        return self

    @property
    def scope(self) -> TranslationScope:
        return _CONTEXT_TO_TRANSLATION_SCOPE[self.context.scope]


class TranslationArtifact(_StrictModel):
    id: str = Field(min_length=1, max_length=80)
    work_id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    document_version_id: str = Field(min_length=1, max_length=80)
    document_content_sha256: str
    context_snapshot_id: str = Field(min_length=1, max_length=80)
    task_id: str = Field(min_length=1, max_length=80)
    job_id: str = Field(min_length=1, max_length=80)
    source_language: TranslationLanguage
    target_language: TranslationLanguage
    scope: TranslationScope
    status: TranslationStatus
    evidence_level: TranslationEvidenceLevel
    prompt_version: str = Field(min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @field_validator(
        "id",
        "work_id",
        "document_version_id",
        "context_snapshot_id",
        "task_id",
        "job_id",
        "prompt_version",
        mode="before",
    )
    @classmethod
    def _safe_identifiers(cls, value: Any, info: Any) -> str:
        return _bounded_text(value, name=info.field_name, maximum=80)

    @field_validator("document_content_sha256")
    @classmethod
    def _valid_document_hash(cls, value: str) -> str:
        if not _HASH.fullmatch(value):
            raise ValueError("document_content_sha256 must be a lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _coherent_projection(self) -> TranslationArtifact:
        if self.source_language == self.target_language:
            raise ValueError("source and target languages must be different")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        terminal = self.status in {
            TranslationStatus.SUCCEEDED,
            TranslationStatus.PARTIAL,
            TranslationStatus.FAILED,
            TranslationStatus.CANCELLED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("completed_at must match terminal translation status")
        return self


_CONTEXT_TO_TRANSLATION_SCOPE = {
    ContextScope.SELECTION: TranslationScope.SELECTION,
    ContextScope.CURRENT_PAGE: TranslationScope.PAGE,
    ContextScope.CURRENT_SECTION: TranslationScope.SECTION,
    ContextScope.FULL_DOCUMENT: TranslationScope.FULL_DOCUMENT,
}

__all__ = [
    "SUPPORTED_LANGUAGES",
    "TranslationArtifact",
    "TranslationCommand",
    "TranslationEvidenceLevel",
    "TranslationLanguage",
    "TranslationScope",
    "TranslationStatus",
    "TranslationTermInput",
    "normalize_source_term",
]
