"""Reviewable annotation migration across immutable PDF versions."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import utc_now
from paper_reader.reader.annotations import (
    AnnotationColor,
    AnnotationInput,
    AnnotationKind,
    PdfRect,
)
from paper_reader.reader.models import (
    AnnotationMigrationBatchRecord,
    AnnotationMigrationCandidateRecord,
    AnnotationMigrationDecisionRecord,
    AnnotationRecord,
    DocumentPageTextRecord,
    LibraryEntryRecord,
)

MIGRATION_ALGORITHM_VERSION = "annotation-migration-v1"
MigrationMatchState = Literal["matched", "ambiguous", "orphaned"]
MigrationConfidence = Literal["high", "medium", "low", "none"]
MigrationDecision = Literal["accept", "reject"]
TextSource = Literal["native", "ocr"]
_LEGAL_ACCESS = {"open", "user_provided"}
_PDF_MEDIA_TYPE = "application/pdf"
_AMBIGUITY_DELTA = 0.02


class MigrationNotFoundError(KeyError):
    """A migration, document, or candidate is outside the local library."""


class MigrationConflictError(RuntimeError):
    """The migration no longer has a safe, current evidence boundary."""


class MigrationCancelledError(RuntimeError):
    """Preview computation was cancelled before any rows were published."""


@dataclass(frozen=True, slots=True)
class MigrationAlternative:
    page_number: int
    page_width: float
    page_height: float
    quote_text: str
    rects: tuple[PdfRect, ...]
    text_source: TextSource
    score: float


@dataclass(frozen=True, slots=True)
class MigrationCandidate:
    id: str
    batch_id: str
    source_annotation_id: str
    source_kind: AnnotationKind
    source_page_number: int
    source_quote_text: str | None
    source_note: str | None
    source_color: AnnotationColor
    match_state: MigrationMatchState
    confidence: MigrationConfidence
    score: float
    target_page_number: int | None
    target_page_width: float | None
    target_page_height: float | None
    target_quote_text: str | None
    target_context_before: str | None
    target_context_after: str | None
    target_rects: tuple[PdfRect, ...]
    text_source: TextSource | None
    alternatives: tuple[MigrationAlternative, ...]
    decision: MigrationDecision | None
    target_annotation_id: str | None
    created_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True, slots=True)
class MigrationCoverage:
    total: int
    matched: int
    ambiguous: int
    orphaned: int
    pending: int
    accepted: int
    rejected: int


@dataclass(frozen=True, slots=True)
class MigrationPreview:
    id: str
    algorithm_version: str
    source_document_version_id: str
    source_document_content_sha256: str
    target_document_version_id: str
    target_document_content_sha256: str
    target_extraction_sha256: str
    candidates: tuple[MigrationCandidate, ...]
    coverage: MigrationCoverage
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _TextBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True, slots=True)
class _Page:
    page_number: int
    width: float
    height: float
    text: str
    source: TextSource
    blocks: tuple[_TextBlock, ...]
    fingerprint_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Occurrence:
    page: _Page
    start: int
    end: int
    quote: str
    before: str
    after: str
    rects: tuple[PdfRect, ...]
    score: float


class AnnotationMigrationService:
    """Create immutable match suggestions and append-only user decisions."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def preview(
        self,
        source_document_id: str,
        target_document_id: str,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> MigrationPreview:
        if cancelled():
            raise MigrationCancelledError("Annotation migration preview was cancelled")
        now = _require_aware(self._clock())
        with self._sessions() as session:
            source, target = self._require_pair(
                session,
                source_document_id=source_document_id,
                target_document_id=target_document_id,
            )
            target_pages = self._target_pages(session, target)
            target_extraction_sha256 = _target_extraction_sha256(target_pages)
            annotations = tuple(
                session.scalars(
                    select(AnnotationRecord)
                    .where(AnnotationRecord.document_version_id == source.id)
                    .order_by(
                        AnnotationRecord.page_number,
                        AnnotationRecord.created_at,
                        AnnotationRecord.id,
                    )
                )
            )
            snapshots: list[tuple[AnnotationRecord, dict[str, object], str]] = []
            for annotation in annotations:
                if cancelled():
                    raise MigrationCancelledError("Annotation migration preview was cancelled")
                if annotation.document_content_sha256 != source.content_sha256:
                    raise MigrationConflictError("Source annotation content hash is stale")
                snapshot = _annotation_snapshot(annotation)
                snapshots.append((annotation, snapshot, _sha256(snapshot)))
            source_annotations_sha256 = _sha256(
                [
                    {"id": annotation.id, "sha256": annotation_sha256}
                    for annotation, _, annotation_sha256 in snapshots
                ]
            )
            preview_key = _sha256(
                {
                    "algorithm": MIGRATION_ALGORITHM_VERSION,
                    "source_document_id": source.id,
                    "source_content_sha256": source.content_sha256,
                    "source_annotations_sha256": source_annotations_sha256,
                    "target_document_id": target.id,
                    "target_content_sha256": target.content_sha256,
                    "target_extraction_sha256": target_extraction_sha256,
                }
            )
            existing = session.scalar(
                select(AnnotationMigrationBatchRecord).where(
                    AnnotationMigrationBatchRecord.preview_key == preview_key
                )
            )
            if existing is not None:
                return self._preview_domain(session, existing)

            batch_id = str(uuid5(NAMESPACE_URL, f"paper-reader:migration:{preview_key}"))
            source_page_count = self._source_page_count(
                session,
                source.id,
                annotations,
            )
            candidate_rows: list[AnnotationMigrationCandidateRecord] = []
            for index, (annotation, snapshot, annotation_sha256) in enumerate(snapshots):
                if cancelled():
                    raise MigrationCancelledError("Annotation migration preview was cancelled")
                match = _match_annotation(
                    annotation,
                    target_pages,
                    source_page_count=source_page_count,
                    cancelled=cancelled,
                )
                candidate_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"paper-reader:migration:{preview_key}:{annotation.id}",
                    )
                )
                candidate_payload = {
                    "source_annotation_sha256": annotation_sha256,
                    **match,
                }
                candidate_rows.append(
                    AnnotationMigrationCandidateRecord(
                        id=candidate_id,
                        batch_id=batch_id,
                        source_annotation_id=annotation.id,
                        source_annotation_sha256=annotation_sha256,
                        source_snapshot=snapshot,
                        order_index=index,
                        match_state=cast(str, match["match_state"]),
                        confidence=cast(str, match["confidence"]),
                        score=cast(float, match["score"]),
                        target_page_number=cast(int | None, match["target_page_number"]),
                        target_page_width=cast(float | None, match["target_page_width"]),
                        target_page_height=cast(float | None, match["target_page_height"]),
                        target_quote_text=cast(str | None, match["target_quote_text"]),
                        target_context_before=cast(str | None, match["target_context_before"]),
                        target_context_after=cast(str | None, match["target_context_after"]),
                        target_rects=cast(list[dict[str, float]], match["target_rects"]),
                        text_source=cast(str | None, match["text_source"]),
                        alternatives=cast(list[dict[str, object]], match["alternatives"]),
                        candidate_sha256=_sha256(candidate_payload),
                        created_at=now,
                    )
                )
            if cancelled():
                raise MigrationCancelledError("Annotation migration preview was cancelled")
            batch = AnnotationMigrationBatchRecord(
                id=batch_id,
                preview_key=preview_key,
                algorithm_version=MIGRATION_ALGORITHM_VERSION,
                source_document_version_id=source.id,
                source_document_content_sha256=source.content_sha256,
                target_document_version_id=target.id,
                target_document_content_sha256=target.content_sha256,
                source_annotations_sha256=source_annotations_sha256,
                target_extraction_sha256=target_extraction_sha256,
                created_at=now,
            )
            session.add(batch)
            session.add_all(candidate_rows)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrent = session.scalar(
                    select(AnnotationMigrationBatchRecord).where(
                        AnnotationMigrationBatchRecord.preview_key == preview_key
                    )
                )
                if concurrent is None:
                    raise
                return self._preview_domain(session, concurrent)
            return self._preview_domain(session, batch)

    def get(self, preview_id: str) -> MigrationPreview:
        with self._sessions() as session:
            batch = session.get(AnnotationMigrationBatchRecord, preview_id)
            if batch is None:
                raise MigrationNotFoundError(preview_id)
            return self._preview_domain(session, batch)

    def decide(
        self,
        candidate_id: str,
        *,
        decision: MigrationDecision,
    ) -> MigrationCandidate:
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be accept or reject")
        now = _require_aware(self._clock())
        with self._sessions() as session:
            candidate = session.get(AnnotationMigrationCandidateRecord, candidate_id)
            if candidate is None:
                raise MigrationNotFoundError(candidate_id)
            batch = session.get(AnnotationMigrationBatchRecord, candidate.batch_id)
            if batch is None:
                raise MigrationNotFoundError(candidate.batch_id)
            if session.get(AnnotationMigrationDecisionRecord, candidate.id) is not None:
                raise MigrationConflictError("Migration candidate has already been decided")
            source, target = self._require_pair(
                session,
                source_document_id=batch.source_document_version_id,
                target_document_id=batch.target_document_version_id,
            )
            if (
                source.content_sha256 != batch.source_document_content_sha256
                or target.content_sha256 != batch.target_document_content_sha256
            ):
                raise MigrationConflictError("Migration document content hash changed")
            target_pages = self._target_pages(session, target)
            if _target_extraction_sha256(target_pages) != batch.target_extraction_sha256:
                raise MigrationConflictError("Target extraction changed after preview")
            source_annotation = session.get(AnnotationRecord, candidate.source_annotation_id)
            if (
                source_annotation is None
                or source_annotation.document_version_id != source.id
                or _sha256(_annotation_snapshot(source_annotation))
                != candidate.source_annotation_sha256
            ):
                raise MigrationConflictError("Source annotation changed after preview")

            target_annotation_id: str | None = None
            if decision == "accept":
                if candidate.match_state == "orphaned":
                    raise MigrationConflictError("Migration candidate has no target match")
                target_annotation = self._accepted_annotation(
                    candidate,
                    target=target,
                    page_count=len(target_pages),
                    now=now,
                )
                target_annotation_id = target_annotation.id
                session.add(target_annotation)
                session.flush()
            decision_record = AnnotationMigrationDecisionRecord(
                candidate_id=candidate.id,
                decision=decision,
                target_annotation_id=target_annotation_id,
                decided_at=now,
            )
            session.add(decision_record)
            session.commit()
            return self._candidate_domain(candidate, decision_record)

    @staticmethod
    def _source_page_count(
        session: Session,
        source_document_id: str,
        annotations: Sequence[AnnotationRecord],
    ) -> int:
        stored_pages = tuple(
            session.scalars(
                select(DocumentPageTextRecord.page_number).where(
                    DocumentPageTextRecord.document_version_id == source_document_id
                )
            )
        )
        annotation_pages = tuple(annotation.page_number for annotation in annotations)
        return max((*stored_pages, *annotation_pages, 1))

    @staticmethod
    def _require_pair(
        session: Session,
        *,
        source_document_id: str,
        target_document_id: str,
    ) -> tuple[DocumentVersionRecord, DocumentVersionRecord]:
        if source_document_id == target_document_id:
            raise MigrationConflictError("Migration requires different document versions")
        source = session.get(DocumentVersionRecord, source_document_id)
        target = session.get(DocumentVersionRecord, target_document_id)
        if source is None:
            raise MigrationNotFoundError(source_document_id)
        if target is None:
            raise MigrationNotFoundError(target_document_id)
        source_work = _canonical_work_id(session, source.work_id)
        target_work = _canonical_work_id(session, target.work_id)
        if source_work != target_work:
            raise MigrationConflictError("Migration documents must belong to the same Work")
        if session.get(LibraryEntryRecord, source_work) is None:
            raise MigrationNotFoundError(source_document_id)
        if (
            target.access_status not in _LEGAL_ACCESS
            or target.media_type.casefold() != _PDF_MEDIA_TYPE
            or target.relative_path is None
        ):
            raise MigrationConflictError("Target document is not available for reading")
        return source, target

    @staticmethod
    def _target_pages(
        session: Session,
        target: DocumentVersionRecord,
    ) -> tuple[_Page, ...]:
        if target.extraction_status != "complete":
            raise MigrationConflictError("Target extraction is not complete")
        rows = tuple(
            session.scalars(
                select(DocumentPageTextRecord)
                .where(DocumentPageTextRecord.document_version_id == target.id)
                .order_by(DocumentPageTextRecord.page_number)
            )
        )
        if not rows or tuple(row.page_number for row in rows) != tuple(range(1, len(rows) + 1)):
            raise MigrationConflictError("Target extraction is not complete")
        return tuple(_page_from_record(row) for row in rows)

    @staticmethod
    def _accepted_annotation(
        candidate: AnnotationMigrationCandidateRecord,
        *,
        target: DocumentVersionRecord,
        page_count: int,
        now: datetime,
    ) -> AnnotationRecord:
        source = candidate.source_snapshot
        kind = AnnotationKind(str(source["kind"]))
        color = AnnotationColor(str(source["color"]))
        rects = tuple(_rect_from_payload(value) for value in candidate.target_rects)
        quote = None if kind is AnnotationKind.BOOKMARK else candidate.target_quote_text
        before = None if kind is AnnotationKind.BOOKMARK else candidate.target_context_before
        after = None if kind is AnnotationKind.BOOKMARK else candidate.target_context_after
        if (
            candidate.target_page_number is None
            or candidate.target_page_width is None
            or candidate.target_page_height is None
        ):
            raise MigrationConflictError("Migration candidate has no target match")
        value = AnnotationInput(
            document_content_sha256=target.content_sha256,
            kind=kind,
            page_number=candidate.target_page_number,
            page_count=page_count,
            page_width=candidate.target_page_width,
            page_height=candidate.target_page_height,
            quote_text=quote,
            context_before=before,
            context_after=after,
            rects=rects,
            color=color,
            note=cast(str | None, source.get("note")),
        )
        return AnnotationRecord(
            id=str(uuid4()),
            document_version_id=target.id,
            document_content_sha256=target.content_sha256,
            kind=value.kind.value,
            page_number=value.page_number,
            page_width=value.page_width,
            page_height=value.page_height,
            quote_text=value.quote_text,
            context_before=value.context_before,
            context_after=value.context_after,
            rects=[_rect_payload(rect) for rect in value.rects],
            color=value.color.value,
            note=value.note,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def _preview_domain(
        cls,
        session: Session,
        batch: AnnotationMigrationBatchRecord,
    ) -> MigrationPreview:
        records = tuple(
            session.scalars(
                select(AnnotationMigrationCandidateRecord)
                .where(AnnotationMigrationCandidateRecord.batch_id == batch.id)
                .order_by(AnnotationMigrationCandidateRecord.order_index)
            )
        )
        decisions = (
            {
                value.candidate_id: value
                for value in session.scalars(
                    select(AnnotationMigrationDecisionRecord).where(
                        AnnotationMigrationDecisionRecord.candidate_id.in_(
                            tuple(record.id for record in records)
                        )
                    )
                )
            }
            if records
            else {}
        )
        candidates = tuple(
            cls._candidate_domain(record, decisions.get(record.id)) for record in records
        )
        coverage = MigrationCoverage(
            total=len(candidates),
            matched=sum(candidate.match_state == "matched" for candidate in candidates),
            ambiguous=sum(candidate.match_state == "ambiguous" for candidate in candidates),
            orphaned=sum(candidate.match_state == "orphaned" for candidate in candidates),
            pending=sum(candidate.decision is None for candidate in candidates),
            accepted=sum(candidate.decision == "accept" for candidate in candidates),
            rejected=sum(candidate.decision == "reject" for candidate in candidates),
        )
        return MigrationPreview(
            id=batch.id,
            algorithm_version=batch.algorithm_version,
            source_document_version_id=batch.source_document_version_id,
            source_document_content_sha256=batch.source_document_content_sha256,
            target_document_version_id=batch.target_document_version_id,
            target_document_content_sha256=batch.target_document_content_sha256,
            target_extraction_sha256=batch.target_extraction_sha256,
            candidates=candidates,
            coverage=coverage,
            created_at=_utc(batch.created_at),
        )

    @staticmethod
    def _candidate_domain(
        record: AnnotationMigrationCandidateRecord,
        decision: AnnotationMigrationDecisionRecord | None,
    ) -> MigrationCandidate:
        source = record.source_snapshot
        return MigrationCandidate(
            id=record.id,
            batch_id=record.batch_id,
            source_annotation_id=record.source_annotation_id,
            source_kind=AnnotationKind(str(source["kind"])),
            source_page_number=int(cast(int | str, source["page_number"])),
            source_quote_text=cast(str | None, source.get("quote_text")),
            source_note=cast(str | None, source.get("note")),
            source_color=AnnotationColor(str(source["color"])),
            match_state=cast(MigrationMatchState, record.match_state),
            confidence=cast(MigrationConfidence, record.confidence),
            score=record.score,
            target_page_number=record.target_page_number,
            target_page_width=record.target_page_width,
            target_page_height=record.target_page_height,
            target_quote_text=record.target_quote_text,
            target_context_before=record.target_context_before,
            target_context_after=record.target_context_after,
            target_rects=tuple(_rect_from_payload(value) for value in record.target_rects),
            text_source=cast(TextSource | None, record.text_source),
            alternatives=tuple(_alternative_from_payload(value) for value in record.alternatives),
            decision=(None if decision is None else cast(MigrationDecision, decision.decision)),
            target_annotation_id=(None if decision is None else decision.target_annotation_id),
            created_at=_utc(record.created_at),
            decided_at=None if decision is None else _utc(decision.decided_at),
        )


def _match_annotation(
    annotation: AnnotationRecord,
    pages: Sequence[_Page],
    *,
    source_page_count: int,
    cancelled: Callable[[], bool],
) -> dict[str, object]:
    if annotation.kind == AnnotationKind.BOOKMARK.value:
        expected = _expected_page(
            annotation.page_number,
            source_page_count=source_page_count,
            target_page_count=len(pages),
        )
        page = pages[expected - 1]
        alternative = {
            "page_number": page.page_number,
            "page_width": page.width,
            "page_height": page.height,
            "quote_text": "",
            "rects": [],
            "text_source": page.source,
            "score": 0.45,
        }
        return {
            "match_state": "matched",
            "confidence": "low",
            "score": 0.45,
            "target_page_number": page.page_number,
            "target_page_width": page.width,
            "target_page_height": page.height,
            "target_quote_text": None,
            "target_context_before": None,
            "target_context_after": None,
            "target_rects": [],
            "text_source": page.source,
            "alternatives": [alternative],
        }

    quote = annotation.quote_text or ""
    normalized_quote, _ = _normalized_with_map(quote)
    if not normalized_quote:
        return _orphaned_match()
    expected_page = _expected_page(
        annotation.page_number,
        source_page_count=source_page_count,
        target_page_count=len(pages),
    )
    occurrences: list[_Occurrence] = []
    for page in pages:
        if cancelled():
            raise MigrationCancelledError("Annotation migration preview was cancelled")
        normalized_page, index_map = _normalized_with_map(page.text)
        offset = 0
        while True:
            found = normalized_page.find(normalized_quote, offset)
            if found < 0:
                break
            normalized_end = found + len(normalized_quote)
            start = index_map[found]
            end = index_map[normalized_end - 1] + 1
            start, end = _include_matching_boundary_punctuation(
                quote,
                page.text,
                start=start,
                end=end,
            )
            before = page.text[max(0, start - 2_000) : start]
            after = page.text[end : end + 2_000]
            context_score = _context_score(annotation, before=before, after=after)
            page_score = 1 - abs(page.page_number - expected_page) / max(
                len(pages) - 1,
                1,
            )
            score = 0.72 + 0.20 * context_score + 0.08 * page_score
            if page.source == "ocr":
                score -= 0.03
            rects = _rects_for_span(page, start=start, end=end)
            if rects:
                occurrences.append(
                    _Occurrence(
                        page=page,
                        start=start,
                        end=end,
                        quote=page.text[start:end],
                        before=before,
                        after=after,
                        rects=rects,
                        score=round(max(0.0, min(1.0, score)), 6),
                    )
                )
            offset = found + 1
    if not occurrences:
        return _orphaned_match()
    occurrences.sort(key=lambda value: (-value.score, value.page.page_number, value.start))
    selected = occurrences[0]
    ambiguous = len(occurrences) > 1 and selected.score - occurrences[1].score <= _AMBIGUITY_DELTA
    confidence: MigrationConfidence
    if ambiguous:
        confidence = "low"
    elif selected.score >= 0.90:
        confidence = "high"
    elif selected.score >= 0.75:
        confidence = "medium"
    else:
        confidence = "low"
    alternatives = [_occurrence_payload(value) for value in occurrences[:5]]
    return {
        "match_state": "ambiguous" if ambiguous else "matched",
        "confidence": confidence,
        "score": selected.score,
        "target_page_number": selected.page.page_number,
        "target_page_width": selected.page.width,
        "target_page_height": selected.page.height,
        "target_quote_text": selected.quote,
        "target_context_before": selected.before[-2_000:],
        "target_context_after": selected.after[:2_000],
        "target_rects": [_rect_payload(rect) for rect in selected.rects],
        "text_source": selected.page.source,
        "alternatives": alternatives,
    }


def _orphaned_match() -> dict[str, object]:
    return {
        "match_state": "orphaned",
        "confidence": "none",
        "score": 0.0,
        "target_page_number": None,
        "target_page_width": None,
        "target_page_height": None,
        "target_quote_text": None,
        "target_context_before": None,
        "target_context_after": None,
        "target_rects": [],
        "text_source": None,
        "alternatives": [],
    }


def _page_from_record(record: DocumentPageTextRecord) -> _Page:
    use_ocr = record.ocr_state == "complete" and record.ocr_text is not None
    text = cast(str, record.ocr_text) if use_ocr else record.native_text
    raw_blocks = record.ocr_blocks if use_ocr else record.native_blocks
    source: TextSource = "ocr" if use_ocr else "native"
    blocks = tuple(
        block
        for value in raw_blocks
        if (block := _safe_block(value, width=record.page_width, height=record.page_height))
        is not None
    )
    fingerprint: dict[str, object] = {
        "page_number": record.page_number,
        "page_width": record.page_width,
        "page_height": record.page_height,
        "text": text,
        "text_source": source,
        "blocks": [
            {
                "x0": block.x0,
                "y0": block.y0,
                "x1": block.x1,
                "y1": block.y1,
                "text": block.text,
            }
            for block in blocks
        ],
        "extractor_version": record.extractor_version,
        "ocr_engine_version": record.ocr_engine_version if use_ocr else None,
        "ocr_model_bundle_id": record.ocr_model_bundle_id if use_ocr else None,
    }
    return _Page(
        page_number=record.page_number,
        width=record.page_width,
        height=record.page_height,
        text=text,
        source=source,
        blocks=blocks,
        fingerprint_payload=fingerprint,
    )


def _safe_block(
    value: Mapping[str, object],
    *,
    width: float,
    height: float,
) -> _TextBlock | None:
    try:
        x0 = float(cast(float | int | str, value["x0"]))
        y0 = float(cast(float | int | str, value["y0"]))
        x1 = float(cast(float | int | str, value["x1"]))
        y1 = float(cast(float | int | str, value["y1"]))
        text = str(value["text"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not text
        or not all(math.isfinite(item) for item in (x0, y0, x1, y1))
        or not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height)
    ):
        return None
    return _TextBlock(x0=x0, y0=y0, x1=x1, y1=y1, text=text)


def _target_extraction_sha256(pages: Sequence[_Page]) -> str:
    return _sha256([page.fingerprint_payload for page in pages])


def _annotation_snapshot(annotation: AnnotationRecord) -> dict[str, object]:
    return {
        "id": annotation.id,
        "document_version_id": annotation.document_version_id,
        "document_content_sha256": annotation.document_content_sha256,
        "kind": annotation.kind,
        "page_number": annotation.page_number,
        "page_width": annotation.page_width,
        "page_height": annotation.page_height,
        "quote_text": annotation.quote_text,
        "context_before": annotation.context_before,
        "context_after": annotation.context_after,
        "rects": annotation.rects,
        "color": annotation.color,
        "note": annotation.note,
    }


def _normalized_with_map(value: str) -> tuple[str, tuple[int, ...]]:
    characters: list[str] = []
    indices: list[int] = []
    for original_index, original in enumerate(value):
        for normalized in unicodedata.normalize("NFKC", original).casefold():
            if normalized.isalnum():
                characters.append(normalized)
                indices.append(original_index)
    return "".join(characters), tuple(indices)


def _context_score(
    annotation: AnnotationRecord,
    *,
    before: str,
    after: str,
) -> float:
    scores: list[float] = []
    source_before, _ = _normalized_with_map(annotation.context_before or "")
    source_after, _ = _normalized_with_map(annotation.context_after or "")
    target_before, _ = _normalized_with_map(before)
    target_after, _ = _normalized_with_map(after)
    if source_before:
        scores.append(
            SequenceMatcher(
                None,
                source_before[-160:],
                target_before[-160:],
                autojunk=False,
            ).ratio()
        )
    if source_after:
        scores.append(
            SequenceMatcher(
                None,
                source_after[:160],
                target_after[:160],
                autojunk=False,
            ).ratio()
        )
    return sum(scores) / len(scores) if scores else 0.5


def _include_matching_boundary_punctuation(
    source_quote: str,
    target_text: str,
    *,
    start: int,
    end: int,
) -> tuple[int, int]:
    alphanumeric = [index for index, character in enumerate(source_quote) if character.isalnum()]
    if not alphanumeric:
        return start, end
    prefix = source_quote[: alphanumeric[0]]
    suffix = source_quote[alphanumeric[-1] + 1 :]
    if prefix and start >= len(prefix) and target_text[start - len(prefix) : start] == prefix:
        start -= len(prefix)
    if suffix and target_text.startswith(suffix, end):
        end += len(suffix)
    return start, end


def _expected_page(
    source_page: int,
    *,
    source_page_count: int,
    target_page_count: int,
) -> int:
    if source_page_count <= 1 or target_page_count <= 1:
        return 1
    ratio = (source_page - 1) / (source_page_count - 1)
    return max(1, min(target_page_count, round(ratio * (target_page_count - 1)) + 1))


def _rects_for_span(page: _Page, *, start: int, end: int) -> tuple[PdfRect, ...]:
    offset = 0
    result: list[PdfRect] = []
    for block in page.blocks:
        block_start = offset
        block_end = offset + len(block.text)
        offset = block_end
        if block_end <= start or block_start >= end:
            continue
        rect = PdfRect(x0=block.x0, y0=block.y0, x1=block.x1, y1=block.y1)
        if rect not in result:
            result.append(rect)
    return tuple(result)


def _occurrence_payload(value: _Occurrence) -> dict[str, object]:
    return {
        "page_number": value.page.page_number,
        "page_width": value.page.width,
        "page_height": value.page.height,
        "quote_text": value.quote,
        "rects": [_rect_payload(rect) for rect in value.rects],
        "text_source": value.page.source,
        "score": value.score,
    }


def _alternative_from_payload(value: Mapping[str, object]) -> MigrationAlternative:
    return MigrationAlternative(
        page_number=int(cast(int | str, value["page_number"])),
        page_width=float(cast(float | int | str, value["page_width"])),
        page_height=float(cast(float | int | str, value["page_height"])),
        quote_text=str(value["quote_text"]),
        rects=tuple(
            _rect_from_payload(item)
            for item in cast(Sequence[Mapping[str, object]], value["rects"])
        ),
        text_source=cast(TextSource, value["text_source"]),
        score=float(cast(float | int | str, value["score"])),
    )


def _rect_payload(rect: PdfRect) -> dict[str, float]:
    return {"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1}


def _rect_from_payload(value: Mapping[str, object]) -> PdfRect:
    return PdfRect(
        x0=float(cast(float | int | str, value["x0"])),
        y0=float(cast(float | int | str, value["y0"])),
        x1=float(cast(float | int | str, value["x1"])),
        y1=float(cast(float | int | str, value["y1"])),
    )


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_work_id(session: Session, work_id: str) -> str:
    work = session.get(WorkRecord, work_id)
    if work is None:
        raise MigrationNotFoundError(work_id)
    visited: set[str] = set()
    while work.status == "merged":
        if work.id in visited or work.merged_into_work_id is None:
            raise MigrationConflictError("Invalid Work merge chain")
        visited.add(work.id)
        target = session.get(WorkRecord, work.merged_into_work_id)
        if target is None:
            raise MigrationConflictError("Merged Work target is missing")
        work = target
    if work.status != "active":
        raise MigrationConflictError("Work is not active")
    return work.id


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
