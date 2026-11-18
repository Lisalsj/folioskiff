"""Deterministic, content-bound evidence bundles for Agent tasks.

Only explicitly selected paper evidence crosses an Agent backend boundary.  Local
paths and storage details deliberately do not appear in the public bundle.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Callable
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.reader.models import AnnotationRecord, DocumentPageTextRecord
from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

if TYPE_CHECKING:
    from paper_reader.agents.vision import VisualEvidenceBatch

_SCHEMA_VERSION = "agent-evidence-v2"
_LEGAL_FULL_TEXT_ACCESS = frozenset({"open", "user_provided"})
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


class EvidenceError(RuntimeError):
    """Base error for evidence selection and validation."""


class EvidenceUnavailableError(EvidenceError):
    """No lawful full text or non-empty abstract is available."""


class EvidenceConflictError(EvidenceError):
    """Persisted evidence no longer agrees with its immutable identity."""


class EvidenceLevel(StrEnum):
    FULL_TEXT = "full_text"
    ABSTRACT_ONLY = "abstract_only"


class EvidenceWork(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    doi: str | None
    published_at: str | None


class EvidenceDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_version_id: str
    content_sha256: str
    access_status: str
    media_type: str
    completeness: int
    extraction_status: str


class EvidencePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    page_number: int = Field(ge=1)
    source: Literal["native", "ocr"]
    text: str
    source_text_sha256: str
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    truncated: bool


class EvidenceAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    annotation_id: str
    document_content_sha256: str
    kind: str
    page_number: int = Field(ge=1)
    quote_text: str | None
    context_before: str | None
    context_after: str | None
    rects: tuple[dict[str, float], ...]
    color: str
    note: str | None


class EvidenceSelectionRect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    x0: float = Field(ge=0, le=100_000)
    y0: float = Field(ge=0, le=100_000)
    x1: float = Field(gt=0, le=100_000)
    y1: float = Field(gt=0, le=100_000)

    @model_validator(mode="after")
    def _positive_area(self) -> EvidenceSelectionRect:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("selection rectangles must have positive area")
        return self


class EvidenceSelectionExcerpt(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    page_number: int = Field(ge=1, le=2_000)
    page_width: float = Field(gt=0, le=100_000)
    page_height: float = Field(gt=0, le=100_000)
    quote_text: str = Field(min_length=1, max_length=50_000)
    context_before: str | None = Field(default=None, max_length=4_000)
    context_after: str | None = Field(default=None, max_length=4_000)
    rects: tuple[EvidenceSelectionRect, ...] = Field(min_length=1, max_length=200)
    verified: bool = False

    @model_validator(mode="after")
    def _valid_geometry(self) -> EvidenceSelectionExcerpt:
        if any(
            rect.x1 > self.page_width
            or rect.y1 > self.page_height
            for rect in self.rects
        ):
            raise ValueError("selection rectangles must remain inside the PDF page")
        if not self.quote_text.strip():
            raise ValueError("selection quote cannot be blank")
        return self


class EvidenceFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_numbers: tuple[int, ...] | None = None
    annotation_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _closed_filters(self) -> EvidenceFilters:
        _validated_page_filter(self.page_numbers)
        _validated_annotation_filter(self.annotation_ids)
        return self


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_pages: int = Field(ge=0)
    available_pages: int = Field(ge=0)
    included_pages: int = Field(ge=0)
    total_characters: int = Field(ge=0)
    included_characters: int = Field(ge=0)
    percentage: int = Field(ge=0, le=100)
    truncated: bool


class EvidenceBundle(BaseModel):
    """A stable JSON payload whose hash covers every supplied evidence byte."""

    model_config = ConfigDict(frozen=True)

    schema_version: str
    work: EvidenceWork
    document: EvidenceDocument | None
    evidence_level: EvidenceLevel
    pages: tuple[EvidencePage, ...]
    annotations: tuple[EvidenceAnnotation, ...]
    filters: EvidenceFilters = Field(default_factory=EvidenceFilters)
    selection_excerpt: EvidenceSelectionExcerpt | None = None
    abstract_evidence: str | None
    coverage: EvidenceCoverage
    warnings: tuple[str, ...]
    research_intent: str | None
    evidence_sha256: str

    @classmethod
    def create(
        cls,
        *,
        work: EvidenceWork,
        document: EvidenceDocument | None,
        evidence_level: EvidenceLevel,
        pages: tuple[EvidencePage, ...],
        annotations: tuple[EvidenceAnnotation, ...],
        coverage: EvidenceCoverage,
        filters: EvidenceFilters | None = None,
        selection_excerpt: EvidenceSelectionExcerpt | None = None,
        research_intent: str | None = None,
        abstract_evidence: str | None = None,
        warnings: tuple[str, ...] = (),
    ) -> EvidenceBundle:
        payload: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "work": work.model_dump(mode="json"),
            "document": (document.model_dump(mode="json") if document is not None else None),
            "evidence_level": evidence_level.value,
            "pages": [page.model_dump(mode="json") for page in pages],
            "annotations": [item.model_dump(mode="json") for item in annotations],
            "filters": (filters or EvidenceFilters()).model_dump(mode="json"),
            "selection_excerpt": (
                selection_excerpt.model_dump(mode="json")
                if selection_excerpt is not None
                else None
            ),
            "abstract_evidence": abstract_evidence,
            "coverage": coverage.model_dump(mode="json"),
            "warnings": list(warnings),
            "research_intent": _optional_text(research_intent),
        }
        evidence_hash = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        return cls(
            **payload,
            evidence_sha256=evidence_hash,
        )

    def to_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))


class EvidenceBundleBuilder:
    """Build an exact, lawful evidence snapshot from the local paper library."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        store: ContentStore,
        character_budget: int = 1_500_000,
    ) -> None:
        if character_budget <= 0:
            raise ValueError("character_budget must be positive")
        self._sessions = sessions
        self._store = store
        self._character_budget = character_budget

    @property
    def store(self) -> ContentStore:
        return self._store

    def build(
        self,
        work_id: str,
        *,
        document_version_id: str | None = None,
        research_intent: str | None = None,
        page_numbers: tuple[int, ...] | None = None,
        annotation_ids: tuple[str, ...] | None = None,
        selection_excerpt: EvidenceSelectionExcerpt | None = None,
    ) -> EvidenceBundle:
        page_filter = _validated_page_filter(page_numbers)
        annotation_filter = _validated_annotation_filter(annotation_ids)
        if (
            selection_excerpt is not None
            and page_filter is not None
            and selection_excerpt.page_number not in page_filter
        ):
            raise ValueError("selection excerpt page must be included in page_numbers")
        filters = EvidenceFilters(
            page_numbers=page_filter,
            annotation_ids=annotation_filter,
        )
        with self._sessions() as session:
            work = self._resolve_work(session, work_id)
            document = self._select_document(
                session,
                work,
                document_version_id=document_version_id,
            )
            public_work = EvidenceWork(
                work_id=work.id,
                title=work.canonical_title,
                authors=tuple(work.authors or ()),
                abstract=_optional_text(work.abstract),
                doi=_optional_text(work.doi),
                published_at=(
                    work.published_at.isoformat() if work.published_at is not None else None
                ),
            )
            public_document = self._public_document(document) if document is not None else None
            annotations = self._annotations(
                session,
                document,
                annotation_ids=annotation_filter,
            )
            full_text_available, unavailable_warning = self._verify_local_document(document)
            if full_text_available and document is not None:
                pages, coverage, warnings = self._pages(
                    session,
                    document,
                    page_numbers=page_filter,
                )
                verified_selection, selection_warnings = self._selection_excerpt(
                    session,
                    document,
                    selection_excerpt,
                )
                warnings = (*warnings, *selection_warnings)
                if (
                    pages
                    or annotations
                    or (verified_selection is not None and verified_selection.verified)
                ):
                    return EvidenceBundle.create(
                        work=public_work,
                        document=public_document,
                        evidence_level=EvidenceLevel.FULL_TEXT,
                        pages=pages,
                        annotations=annotations,
                        coverage=coverage,
                        warnings=warnings,
                        research_intent=research_intent,
                        filters=filters,
                        selection_excerpt=verified_selection,
                    )
                unavailable_warning = "extracted_text_unavailable"

            abstract = _optional_text(work.abstract)
            if abstract is None:
                raise EvidenceUnavailableError(
                    "No lawful full text or non-empty abstract evidence is available"
                )
            fallback_warnings = ["full_text_unavailable"]
            if unavailable_warning is not None:
                fallback_warnings.append(unavailable_warning)
            if selection_excerpt is not None and full_text_available:
                fallback_warnings.append("selection_unverified")
            if page_filter is not None or selection_excerpt is not None:
                fallback_warnings.append("abstract_fallback_no_page_citations")
            return EvidenceBundle.create(
                work=public_work,
                document=public_document,
                evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                pages=(),
                annotations=annotations,
                abstract_evidence=abstract,
                coverage=EvidenceCoverage(
                    total_pages=0,
                    available_pages=0,
                    included_pages=0,
                    total_characters=len(abstract),
                    included_characters=len(abstract),
                    percentage=100,
                    truncated=False,
                ),
                warnings=tuple(fallback_warnings),
                research_intent=research_intent,
                filters=filters,
                selection_excerpt=None,
            )

    def render_visual_evidence(
        self,
        evidence: EvidenceBundle,
        *,
        supports_vision: bool,
        enabled: bool = True,
        text_extraction_sufficient: bool | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> VisualEvidenceBatch:
        """Resolve the frozen PDF and render only deterministic referenced pages."""

        from paper_reader.agents.vision import (
            VisualEvidenceBatch,
            VisualEvidencePolicy,
            render_visual_evidence,
            select_visual_pages,
        )

        policy = VisualEvidencePolicy(enabled=enabled)
        selections = select_visual_pages(
            evidence,
            supports_vision=supports_vision,
            policy=policy,
            text_extraction_sufficient=text_extraction_sufficient,
        )
        if not selections:
            document = evidence.document
            return VisualEvidenceBatch(
                document_content_sha256=(document.content_sha256 if document is not None else None)
            )
        path = self.resolve_local_pdf(evidence)
        return render_visual_evidence(
            path,
            evidence,
            supports_vision=supports_vision,
            policy=policy,
            text_extraction_sufficient=text_extraction_sufficient,
            cancelled=cancelled,
        )

    def resolve_local_pdf(self, evidence: EvidenceBundle) -> Path:
        """Return a verified store path for the exact frozen evidence document."""

        public = evidence.document
        if public is None:
            raise EvidenceUnavailableError("Visual evidence has no selected document")
        with self._sessions() as session:
            document = session.get(DocumentVersionRecord, public.document_version_id)
            if document is None:
                raise EvidenceUnavailableError("Visual evidence document was not found")
            canonical_work = self._resolve_work(session, document.work_id)
            if canonical_work.id != evidence.work.work_id:
                raise EvidenceConflictError("Visual evidence document belongs to another Work")
            if (
                document.content_sha256 != public.content_sha256
                or document.access_status != public.access_status
                or document.media_type != public.media_type
                or document.extraction_status != public.extraction_status
            ):
                raise EvidenceConflictError(
                    "Visual evidence document no longer matches its frozen identity"
                )
            available, warning = self._verify_local_document(document)
            if not available:
                raise EvidenceUnavailableError(
                    f"Visual evidence PDF is unavailable: {warning or 'unknown'}"
                )
            assert document.relative_path is not None
            try:
                return self._store.resolve_object(
                    document.content_sha256,
                    document.relative_path,
                )
            except UnsafeStoredPathError as error:  # pragma: no cover - rechecked above
                raise EvidenceConflictError("Persisted document path is unsafe") from error

    def _resolve_work(self, session: Session, work_id: str) -> WorkRecord:
        visited: set[str] = set()
        current_id = work_id
        while True:
            if current_id in visited:
                raise EvidenceConflictError("Work merge chain contains a cycle")
            visited.add(current_id)
            work = session.get(WorkRecord, current_id)
            if work is None:
                raise EvidenceUnavailableError("Selected Work was not found")
            if work.status == "active":
                return work
            if work.status != "merged" or not work.merged_into_work_id:
                raise EvidenceConflictError("Work merge state is invalid")
            current_id = work.merged_into_work_id

    def _select_document(
        self,
        session: Session,
        work: WorkRecord,
        *,
        document_version_id: str | None,
    ) -> DocumentVersionRecord | None:
        if document_version_id is not None:
            document = session.get(DocumentVersionRecord, document_version_id)
            if document is None:
                raise EvidenceUnavailableError("Selected document version was not found")
            document_work = self._resolve_work(session, document.work_id)
            if document_work.id != work.id:
                raise EvidenceConflictError("Document version does not belong to the selected Work")
            return document
        return session.scalar(
            select(DocumentVersionRecord)
            .where(DocumentVersionRecord.work_id == work.id)
            .order_by(
                DocumentVersionRecord.completeness.desc(),
                DocumentVersionRecord.source_updated_at.desc(),
                DocumentVersionRecord.created_at.desc(),
                DocumentVersionRecord.id,
            )
            .limit(1)
        )

    @staticmethod
    def _public_document(document: DocumentVersionRecord) -> EvidenceDocument:
        return EvidenceDocument(
            document_version_id=document.id,
            content_sha256=document.content_sha256,
            access_status=document.access_status,
            media_type=document.media_type,
            completeness=document.completeness,
            extraction_status=document.extraction_status,
        )

    def _verify_local_document(
        self,
        document: DocumentVersionRecord | None,
    ) -> tuple[bool, str | None]:
        if document is None:
            return False, "document_version_unavailable"
        if document.access_status not in _LEGAL_FULL_TEXT_ACCESS:
            return False, "full_text_access_restricted"
        if document.media_type.casefold() != "application/pdf":
            return False, "unsupported_document_media_type"
        if document.extraction_status != "complete":
            return False, "text_extraction_incomplete"
        if not document.relative_path:
            return False, "local_document_unavailable"
        try:
            path = self._store.resolve_object(
                document.content_sha256,
                document.relative_path,
            )
        except UnsafeStoredPathError as error:
            raise EvidenceConflictError("Persisted document path is unsafe") from error
        if not path.is_file():
            return False, "local_document_unavailable"
        actual_hash = _file_sha256(path)
        if actual_hash != document.content_sha256.casefold():
            raise EvidenceConflictError(
                "Local document content hash does not match the selected version"
            )
        return True, None

    @staticmethod
    def _annotations(
        session: Session,
        document: DocumentVersionRecord | None,
        *,
        annotation_ids: tuple[str, ...] | None,
    ) -> tuple[EvidenceAnnotation, ...]:
        if document is None:
            if annotation_ids:
                raise EvidenceUnavailableError("Selected annotations have no document version")
            return ()
        statement = select(AnnotationRecord).where(
            AnnotationRecord.document_version_id == document.id
        )
        if annotation_ids is not None:
            if not annotation_ids:
                return ()
            statement = statement.where(AnnotationRecord.id.in_(annotation_ids))
        records = tuple(
            session.scalars(
                statement.order_by(
                    AnnotationRecord.page_number,
                    AnnotationRecord.created_at,
                    AnnotationRecord.id,
                )
            )
        )
        if annotation_ids is not None and {record.id for record in records} != set(annotation_ids):
            raise EvidenceUnavailableError(
                "One or more selected annotations are unavailable for this document"
            )
        result: list[EvidenceAnnotation] = []
        for record in records:
            if record.document_content_sha256 != document.content_sha256:
                raise EvidenceConflictError(
                    "Stored annotation is bound to a different document content hash"
                )
            result.append(
                EvidenceAnnotation(
                    evidence_id=f"annotation:{record.id}",
                    annotation_id=record.id,
                    document_content_sha256=record.document_content_sha256,
                    kind=record.kind,
                    page_number=record.page_number,
                    quote_text=record.quote_text,
                    context_before=record.context_before,
                    context_after=record.context_after,
                    rects=tuple(record.rects or ()),
                    color=record.color,
                    note=record.note,
                )
            )
        return tuple(result)

    def _pages(
        self,
        session: Session,
        document: DocumentVersionRecord,
        *,
        page_numbers: tuple[int, ...] | None,
    ) -> tuple[tuple[EvidencePage, ...], EvidenceCoverage, tuple[str, ...]]:
        statement = select(DocumentPageTextRecord).where(
            DocumentPageTextRecord.document_version_id == document.id
        )
        if page_numbers is not None:
            if not page_numbers:
                records: tuple[DocumentPageTextRecord, ...] = ()
            else:
                records = tuple(
                    session.scalars(
                        statement.where(
                            DocumentPageTextRecord.page_number.in_(page_numbers)
                        ).order_by(DocumentPageTextRecord.page_number)
                    )
                )
        else:
            records = tuple(
                session.scalars(statement.order_by(DocumentPageTextRecord.page_number))
            )
        candidates: list[tuple[DocumentPageTextRecord, Literal["native", "ocr"], str]] = []
        missing_scanned_ocr = False
        for record in records:
            native = _optional_text(record.native_text)
            ocr = _optional_text(record.ocr_text)
            if record.likely_scanned and record.ocr_state == "complete" and ocr:
                candidates.append((record, "ocr", ocr))
            elif native:
                candidates.append((record, "native", native))
                if record.likely_scanned and not ocr:
                    missing_scanned_ocr = True
            elif record.likely_scanned:
                missing_scanned_ocr = True

        total_characters = sum(len(text) for _, _, text in candidates)
        remaining = self._character_budget
        pages: list[EvidencePage] = []
        for record, source, source_text in candidates:
            if remaining <= 0:
                break
            included_text = source_text[:remaining]
            truncated = len(included_text) < len(source_text)
            pages.append(
                EvidencePage(
                    evidence_id=f"page:{record.page_number}:{source}",
                    page_number=record.page_number,
                    source=source,
                    text=included_text,
                    source_text_sha256=sha256(source_text.encode("utf-8")).hexdigest(),
                    page_width=record.page_width,
                    page_height=record.page_height,
                    confidence=record.ocr_confidence if source == "ocr" else None,
                    truncated=truncated,
                )
            )
            remaining -= len(included_text)

        included_characters = sum(len(page.text) for page in pages)
        budget_truncated = included_characters < total_characters
        warnings: list[str] = []
        if page_numbers is not None and {record.page_number for record in records} != set(
            page_numbers
        ):
            warnings.append("requested_pages_unavailable")
        if missing_scanned_ocr:
            warnings.append("scanned_pages_without_ocr")
        if budget_truncated:
            warnings.append("evidence_character_budget_reached")
        total_pages = len(page_numbers) if page_numbers is not None else len(records)
        percentage = round(len(candidates) / total_pages * 100) if total_pages else 0
        return (
            tuple(pages),
            EvidenceCoverage(
                total_pages=total_pages,
                available_pages=len(candidates),
                included_pages=len(pages),
                total_characters=total_characters,
                included_characters=included_characters,
                percentage=percentage,
                truncated=budget_truncated,
            ),
            tuple(warnings),
        )

    @staticmethod
    def _selection_excerpt(
        session: Session,
        document: DocumentVersionRecord,
        excerpt: EvidenceSelectionExcerpt | None,
    ) -> tuple[EvidenceSelectionExcerpt | None, tuple[str, ...]]:
        if excerpt is None:
            return None, ()
        record = session.get(
            DocumentPageTextRecord,
            (document.id, excerpt.page_number),
        )
        if record is None:
            return excerpt.model_copy(update={"verified": False}), (
                "selection_unverified",
            )
        if not (
            math.isclose(record.page_width, excerpt.page_width, rel_tol=0, abs_tol=0.01)
            and math.isclose(record.page_height, excerpt.page_height, rel_tol=0, abs_tol=0.01)
        ):
            raise EvidenceConflictError(
                "Selected PDF coordinates do not match the frozen document page"
            )
        native = _optional_text(record.native_text)
        ocr = _optional_text(record.ocr_text)
        source_text = (
            ocr
            if record.likely_scanned and record.ocr_state == "complete" and ocr
            else native
        )
        verified = bool(
            source_text
            and _normalized_excerpt(excerpt.quote_text)
            in _normalized_excerpt(source_text)
        )
        return (
            excerpt.model_copy(update={"verified": verified}),
            () if verified else ("selection_unverified",),
        )


def canonical_json(value: Any) -> str:
    """Serialize JSON-compatible data with a stable byte representation."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def escape_untrusted_prompt_text(value: str) -> str:
    """Encode prompt delimiters so supplied data cannot close a trusted section."""

    return (
        value.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def untrusted_prompt_json(value: Any) -> str:
    """Return deterministic JSON encoded for an inert prompt data section."""

    return escape_untrusted_prompt_text(canonical_json(value))


def _validated_page_filter(value: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple):
        raise ValueError("page_numbers must be a tuple")
    if (
        len(value) > 200
        or any(isinstance(page, bool) or not isinstance(page, int) for page in value)
        or any(page < 1 or page > 2_000 for page in value)
        or value != tuple(sorted(set(value)))
    ):
        raise ValueError(
            "page_numbers must contain at most 200 unique sorted values from 1 to 2,000"
        )
    return value


def _validated_annotation_filter(
    value: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple):
        raise ValueError("annotation_ids must be a tuple")
    if len(value) > 500:
        raise ValueError("annotation_ids cannot contain more than 500 values")
    normalized: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or len(item) > 80
            or _CONTROL_CHARACTER.search(item)
        ):
            raise ValueError("annotation IDs must be safe non-blank identifiers")
        normalized.append(item)
    if tuple(normalized) != tuple(sorted(set(normalized))):
        raise ValueError("annotation_ids must be unique and sorted")
    return tuple(normalized)


def _normalized_excerpt(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value).strip()
    return normalized or None


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
