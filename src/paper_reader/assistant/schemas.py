"""Strict, evidence-bound structured replies for the reader Assistant."""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from paper_reader.agents.evidence import EvidenceBundle, canonical_json
from paper_reader.assistant.repository import (
    AssistantContextSnapshot,
    ContextSnapshotDraft,
)

LocalCitationKind = Literal[
    "abstract",
    "page",
    "figure",
    "table",
    "equation",
    "annotation",
]
_PAGE_KINDS = frozenset({"page", "figure", "table", "equation"})
_LOCATOR_KINDS = frozenset({"figure", "table", "equation"})
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_SafeText = Annotated[str, Field(min_length=1, max_length=20_000)]
AssistantSnapshot = AssistantContextSnapshot | ContextSnapshotDraft


class AssistantReplyEvidenceError(ValueError):
    """A reply cites evidence outside its immutable Assistant snapshot."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class AssistantCitation(_StrictModel):
    """A local citation whose identity must be resolved against the snapshot."""

    evidence_id: str = Field(min_length=1, max_length=200)
    kind: LocalCitationKind | None = None
    page_number: int | None = Field(default=None, ge=1, le=2_000)
    locator: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("evidence_id")
    @classmethod
    def _safe_evidence_id(cls, value: str) -> str:
        if _CONTROL_CHARACTER.search(value):
            raise ValueError("citation evidence_id cannot contain control characters")
        return value

    @model_validator(mode="after")
    def _typed_local_reference(self) -> AssistantCitation:
        if self.kind == "abstract":
            if self.evidence_id != "abstract:work":
                raise ValueError("abstract citation evidence_id must be abstract:work")
            if self.page_number is not None or self.locator is not None:
                raise ValueError("abstract citations cannot contain page or locator fields")
            return self
        if self.kind == "annotation":
            if not self.evidence_id.startswith("annotation:"):
                raise ValueError("annotation citation evidence_id must begin with annotation:")
            if self.locator is not None:
                raise ValueError("annotation citations cannot contain a locator")
            return self
        if self.kind in _PAGE_KINDS:
            if not self.evidence_id.startswith("page:"):
                raise ValueError("page citation evidence_id must begin with page:")
            if self.kind in _LOCATOR_KINDS and self.locator is None:
                raise ValueError(f"{self.kind} citations require a locator")
            if self.kind == "page" and self.locator is not None:
                raise ValueError("page citations cannot contain a locator")
            return self
        if self.locator is not None:
            raise ValueError(
                "citation locator requires an explicit figure, table, or equation kind"
            )
        return self


class AssistantReply(_StrictModel):
    """Backend-independent reply committed only after local evidence validation."""

    answer: str = Field(min_length=1, max_length=200_000)
    citations: tuple[AssistantCitation, ...] = Field(max_length=200)
    uncertainties: tuple[_SafeText, ...] = Field(max_length=50)
    follow_up_questions: tuple[_SafeText, ...] = Field(max_length=8)


def assistant_reply_json_schema() -> dict[str, object]:
    """Return the closed backend-facing validation schema."""

    return AssistantReply.model_json_schema(mode="validation")


def validate_assistant_reply(
    value: AssistantReply | Mapping[str, Any],
    snapshot: AssistantSnapshot,
) -> AssistantReply:
    """Parse a reply and reject every reference outside the frozen snapshot."""

    reply = value if isinstance(value, AssistantReply) else AssistantReply.model_validate(value)
    evidence = _validated_snapshot_evidence(snapshot)
    pages = {item.evidence_id: item for item in evidence.pages}
    annotations = {item.evidence_id: item for item in evidence.annotations}
    abstract_available = bool(evidence.abstract_evidence or evidence.work.abstract)

    for citation in reply.citations:
        if citation.kind == "abstract" or citation.evidence_id == "abstract:work":
            if citation.evidence_id != "abstract:work" or not abstract_available:
                raise AssistantReplyEvidenceError(
                    "citation references an unavailable abstract outside the snapshot"
                )
            continue

        annotation = annotations.get(citation.evidence_id)
        if citation.kind == "annotation" or annotation is not None:
            if annotation is None:
                raise AssistantReplyEvidenceError(
                    f"citation {citation.evidence_id!r} is outside the snapshot"
                )
            if citation.kind not in {None, "annotation"}:
                raise AssistantReplyEvidenceError("citation kind does not match snapshot evidence")
            if citation.page_number is not None and citation.page_number != annotation.page_number:
                raise AssistantReplyEvidenceError(
                    "citation page number does not match the snapshot"
                )
            continue

        page = pages.get(citation.evidence_id)
        if page is None:
            raise AssistantReplyEvidenceError(
                f"citation {citation.evidence_id!r} is outside the snapshot"
            )
        if citation.kind not in {None, *_PAGE_KINDS}:
            raise AssistantReplyEvidenceError("citation kind does not match snapshot evidence")
        if citation.page_number is not None and citation.page_number != page.page_number:
            raise AssistantReplyEvidenceError("citation page number does not match the snapshot")

    return reply


def _validated_snapshot_evidence(snapshot: AssistantSnapshot) -> EvidenceBundle:
    raw = dict(snapshot.evidence)
    embedded_hash = raw.pop("evidence_sha256", None)
    calculated_hash = sha256(canonical_json(raw).encode("utf-8")).hexdigest()
    if embedded_hash != snapshot.evidence_sha256 or calculated_hash != snapshot.evidence_sha256:
        raise AssistantReplyEvidenceError("snapshot evidence identity is invalid")

    evidence = EvidenceBundle.model_validate(snapshot.evidence)
    if evidence.work.work_id != snapshot.work_id:
        raise AssistantReplyEvidenceError("snapshot Work identity is invalid")
    if (
        evidence.document is None
        or evidence.document.document_version_id != snapshot.document_version_id
        or evidence.document.content_sha256 != snapshot.document_content_sha256
    ):
        raise AssistantReplyEvidenceError("snapshot document identity is invalid")
    return evidence


__all__ = [
    "AssistantCitation",
    "AssistantReply",
    "AssistantReplyEvidenceError",
    "assistant_reply_json_schema",
    "validate_assistant_reply",
]
