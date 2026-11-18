"""Closed contracts for explicit related-paper and knowledge-resource retrieval."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class PaperVerificationStatus(StrEnum):
    VERIFIED = "verified"
    PENDING_VERIFICATION = "pending_verification"
    REJECTED = "rejected"


class ResourceVerificationStatus(StrEnum):
    VERIFIED = "verified"
    PENDING_VERIFICATION = "pending_verification"
    REJECTED = "rejected"


class RelatedSetStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RelatedRunSourceStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class KnowledgeResourceType(StrEnum):
    OFFICIAL_DOCS = "official_docs"
    REVIEW = "review"
    COURSE = "course"
    TEXTBOOK = "textbook"
    DATABASE = "database"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def normalize_intent_term(value: str) -> str:
    """Return the stable identity used to detect ambiguous intent terms."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _bounded_text(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} cannot contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return normalized


def _bounded_terms(value: Any, *, name: str, maximum_items: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a collection of terms")
    try:
        raw_terms = tuple(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a collection of terms") from error
    if len(raw_terms) > maximum_items:
        raise ValueError(f"{name} may contain at most {maximum_items} terms")
    terms = tuple(_bounded_text(item, name=name, maximum=120) for item in raw_terms)
    identities = tuple(normalize_intent_term(item) for item in terms)
    if len(identities) != len(set(identities)):
        raise ValueError(f"{name} cannot contain duplicate terms")
    return terms


class RelatedIntentDiff(_StrictModel):
    """User-visible semantic changes from the immediately preceding revision."""

    added_terms: tuple[str, ...] = Field(default=(), max_length=320)
    removed_terms: tuple[str, ...] = Field(default=(), max_length=320)
    moved_terms: tuple[str, ...] = Field(default=(), max_length=320)
    added_source_ids: tuple[str, ...] = Field(default=(), max_length=10)
    removed_source_ids: tuple[str, ...] = Field(default=(), max_length=10)
    summary_changed: bool = False
    recent_months_changed: bool = False

    @field_validator("added_terms", "removed_terms", "moved_terms", mode="before")
    @classmethod
    def _safe_terms(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _bounded_terms(value, name=info.field_name, maximum_items=320)

    @field_validator("added_source_ids", "removed_source_ids", mode="before")
    @classmethod
    def _safe_diff_sources(cls, value: Any, info: Any) -> tuple[str, ...]:
        terms = _bounded_terms(value, name=info.field_name, maximum_items=10)
        normalized = tuple(item.casefold() for item in terms)
        if any(_SAFE_IDENTIFIER.fullmatch(item) is None for item in normalized):
            raise ValueError(f"{info.field_name} must contain safe source identifiers")
        return normalized


class RelatedSearchIntent(_StrictModel):
    set_id: str | None = Field(default=None, max_length=80)
    summary: str = Field(min_length=1, max_length=2_000)
    required_groups: tuple[tuple[str, ...], ...] = Field(min_length=1, max_length=12)
    optional_terms: tuple[str, ...] = Field(default=(), max_length=40)
    excluded_terms: tuple[str, ...] = Field(default=(), max_length=40)
    recent_months: int = Field(default=60, ge=1, le=120)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    revision: int = Field(default=1, ge=1)
    confirmed: bool = False
    confirmed_at: datetime | None = None
    diff: RelatedIntentDiff = Field(default_factory=RelatedIntentDiff)

    @field_validator("set_id", mode="before")
    @classmethod
    def _safe_set_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        identifier = _bounded_text(value, name="set_id", maximum=80)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", identifier):
            raise ValueError("set_id must be a safe identifier")
        return identifier

    @field_validator("summary", mode="before")
    @classmethod
    def _safe_summary(cls, value: Any) -> str:
        return _bounded_text(value, name="summary", maximum=2_000)

    @field_validator("required_groups", mode="before")
    @classmethod
    def _safe_required_groups(cls, value: Any) -> tuple[tuple[str, ...], ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("required_groups must be a collection of concept groups")
        try:
            raw_groups = tuple(value)
        except TypeError as error:
            raise ValueError("required_groups must be a collection of concept groups") from error
        if not 1 <= len(raw_groups) <= 12:
            raise ValueError("required_groups must contain between 1 and 12 groups")
        groups = tuple(
            _bounded_terms(group, name="required group", maximum_items=20) for group in raw_groups
        )
        if any(not group for group in groups):
            raise ValueError("each required group must contain between 1 and 20 terms")
        return groups

    @field_validator("optional_terms", mode="before")
    @classmethod
    def _safe_optional_terms(cls, value: Any) -> tuple[str, ...]:
        return _bounded_terms(value, name="optional_terms", maximum_items=40)

    @field_validator("excluded_terms", mode="before")
    @classmethod
    def _safe_excluded_terms(cls, value: Any) -> tuple[str, ...]:
        return _bounded_terms(value, name="excluded_terms", maximum_items=40)

    @field_validator("source_ids", mode="before")
    @classmethod
    def _safe_sources(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("source_ids must be a collection")
        try:
            raw_sources = tuple(value)
        except TypeError as error:
            raise ValueError("source_ids must be a collection") from error
        if not 1 <= len(raw_sources) <= 10:
            raise ValueError("source_ids must contain between 1 and 10 values")
        sources: list[str] = []
        for value_item in raw_sources:
            source = _bounded_text(value_item, name="source_id", maximum=80).casefold()
            if not _SAFE_IDENTIFIER.fullmatch(source):
                raise ValueError("source_id must be a safe connector identifier")
            sources.append(source)
        if len(sources) != len(set(sources)):
            raise ValueError("source_ids cannot contain duplicates")
        return tuple(sources)

    @model_validator(mode="after")
    def _coherent_intent(self) -> RelatedSearchIntent:
        required_terms = tuple(
            normalize_intent_term(term) for group in self.required_groups for term in group
        )
        required = set(required_terms)
        optional = {normalize_intent_term(term) for term in self.optional_terms}
        excluded = {normalize_intent_term(term) for term in self.excluded_terms}
        if len(required_terms) != len(required):
            raise ValueError("required_groups cannot contain duplicate terms")
        if required & optional or required & excluded or optional & excluded:
            raise ValueError("intent terms cannot appear in conflicting groups")
        if self.confirmed != (self.confirmed_at is not None):
            raise ValueError("confirmed and confirmed_at must be set together")
        if self.confirmed_at is not None and self.confirmed_at.utcoffset() is None:
            raise ValueError("confirmed_at must include a timezone")
        return self


class RelatedSearchCommand(_StrictModel):
    intent: RelatedSearchIntent
    include_academic_sources: bool = True
    include_external_web: bool = False
    external_web_consent: bool = False

    @model_validator(mode="after")
    def _explicit_boundaries(self) -> RelatedSearchCommand:
        if not self.intent.confirmed:
            raise ValueError("intent must be confirmed before retrieval")
        if not self.include_academic_sources and not self.include_external_web:
            raise ValueError("at least one retrieval target must be selected")
        if self.include_external_web and not self.external_web_consent:
            raise ValueError("external web research requires explicit consent")
        if self.external_web_consent and not self.include_external_web:
            raise ValueError("external web consent requires include_external_web=true")
        return self


__all__ = [
    "KnowledgeResourceType",
    "PaperVerificationStatus",
    "RelatedIntentDiff",
    "RelatedRunSourceStatus",
    "RelatedSearchCommand",
    "RelatedSearchIntent",
    "RelatedSetStatus",
    "ResourceVerificationStatus",
    "normalize_intent_term",
]
