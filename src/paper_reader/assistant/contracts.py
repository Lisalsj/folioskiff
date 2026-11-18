"""Closed public contracts for explicit paper Assistant tasks."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_MAX_PAGE_NUMBER = 1_000_000
_MAX_PAGE_DIMENSION = 100_000
AssistantOutputLanguage = Literal["zh-CN", "en", "bilingual"]


class AssistantMode(StrEnum):
    ASK = "ask"
    EXPLAIN = "explain"
    TRANSLATE = "translate"
    RELATED = "related"


class ContextScope(StrEnum):
    SELECTION = "selection"
    CURRENT_PAGE = "current_page"
    CURRENT_SECTION = "current_section"
    FULL_DOCUMENT = "full_document"
    ANNOTATIONS = "annotations"


class NetworkPolicy(StrEnum):
    LOCAL_ONLY = "local_only"
    EXTERNAL_RESEARCH = "external_research"


class AssistantTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def _bounded_identifier(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError(f"{name} cannot contain control characters")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be non-blank and at most {maximum} characters")
    return normalized


class SelectionRect(_StrictModel):
    """One canonical PDF-coordinate rectangle before viewport rotation or scaling."""

    x0: float = Field(ge=0, le=_MAX_PAGE_DIMENSION)
    y0: float = Field(ge=0, le=_MAX_PAGE_DIMENSION)
    x1: float = Field(gt=0, le=_MAX_PAGE_DIMENSION)
    y1: float = Field(gt=0, le=_MAX_PAGE_DIMENSION)

    @model_validator(mode="after")
    def _positive_area(self) -> SelectionRect:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("selection rectangles must have positive area")
        return self


class TextSelectionContext(_StrictModel):
    page_number: int = Field(ge=1, le=_MAX_PAGE_NUMBER)
    page_width: float = Field(gt=0, le=_MAX_PAGE_DIMENSION)
    page_height: float = Field(gt=0, le=_MAX_PAGE_DIMENSION)
    quote_text: str = Field(min_length=1, max_length=50_000)
    context_before: str | None = Field(default=None, max_length=4_000)
    context_after: str | None = Field(default=None, max_length=4_000)
    rects: tuple[SelectionRect, ...] = Field(min_length=1, max_length=200)

    @field_validator("quote_text")
    @classmethod
    def _quote_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("selection quote cannot be blank")
        return normalized

    @field_validator("context_before", "context_after")
    @classmethod
    def _normalize_optional_context(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _rectangles_stay_on_page(self) -> TextSelectionContext:
        if any(rect.x1 > self.page_width or rect.y1 > self.page_height for rect in self.rects):
            raise ValueError("selection rectangles must remain inside page dimensions")
        return self


class AgentSelection(_StrictModel):
    backend_id: str = Field(min_length=1, max_length=80)
    model_id: str | None = Field(default=None, max_length=200)
    reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    service_tier: ServiceTier = ServiceTier.FLEX

    @field_validator("backend_id", "model_id", mode="before")
    @classmethod
    def _safe_identifiers(cls, value: Any, info: Any) -> str | None:
        if value is None:
            return None
        maximum = 80 if info.field_name == "backend_id" else 200
        return _bounded_identifier(value, name=info.field_name, maximum=maximum)


class ContextSelection(_StrictModel):
    document_version_id: str = Field(min_length=1, max_length=80)
    scope: ContextScope
    page_number: int | None = Field(default=None, ge=1, le=_MAX_PAGE_NUMBER)
    selection: TextSelectionContext | None = None
    annotation_ids: tuple[str, ...] = Field(default=(), max_length=500)

    @field_validator("document_version_id", mode="before")
    @classmethod
    def _safe_document_id(cls, value: Any) -> str:
        return _bounded_identifier(value, name="document_version_id", maximum=80)

    @field_validator("annotation_ids", mode="before")
    @classmethod
    def _safe_annotation_ids(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("annotation_ids must be a collection")
        try:
            normalized = tuple(
                _bounded_identifier(item, name="annotation_id", maximum=80) for item in value
            )
        except TypeError as error:
            raise ValueError("annotation_ids must be a collection") from error
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate annotation IDs are not allowed")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def _scope_fields_are_exact(self) -> ContextSelection:
        if self.scope is ContextScope.SELECTION:
            if self.selection is None or self.page_number is not None or self.annotation_ids:
                raise ValueError("selection scope requires only a selection")
            return self
        if self.scope in {ContextScope.CURRENT_PAGE, ContextScope.CURRENT_SECTION}:
            if self.page_number is None or self.selection is not None or self.annotation_ids:
                raise ValueError("page and section scope require only page_number")
            return self
        if self.scope is ContextScope.FULL_DOCUMENT:
            if self.page_number is not None or self.selection is not None or self.annotation_ids:
                raise ValueError("full_document scope cannot include scoped context fields")
            return self
        if not self.annotation_ids or self.page_number is not None or self.selection is not None:
            raise ValueError("annotations scope requires only annotation_ids")
        return self


class AssistantTaskCommand(_StrictModel):
    mode: AssistantMode
    message: str = Field(min_length=1, max_length=20_000)
    context: ContextSelection
    agent: AgentSelection
    network_policy: NetworkPolicy = NetworkPolicy.LOCAL_ONLY
    output_language: AssistantOutputLanguage = "zh-CN"

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _quick_assistant_is_local_only(self) -> AssistantTaskCommand:
        if (
            self.mode in {AssistantMode.ASK, AssistantMode.EXPLAIN}
            and self.network_policy is not NetworkPolicy.LOCAL_ONLY
        ):
            raise ValueError("ask and explain tasks require network_policy=local_only")
        return self


__all__ = [
    "AgentSelection",
    "AssistantMode",
    "AssistantOutputLanguage",
    "AssistantTaskCommand",
    "AssistantTaskStatus",
    "ContextScope",
    "ContextSelection",
    "NetworkPolicy",
    "SelectionRect",
    "TextSelectionContext",
]
