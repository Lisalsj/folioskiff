"""Persistent PDF reading state and immutable-document annotations."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DocumentVersionRecord,
    LibraryStateRecord,
    WorkRecord,
)
from paper_reader.jobs.models import utc_now
from paper_reader.reader.models import (
    AnnotationRecord,
    LibraryEntryRecord,
    ReadingPositionRecord,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LEGAL_ACCESS = {"open", "user_provided"}
_PDF_MEDIA_TYPE = "application/pdf"


class AnnotationNotFoundError(KeyError):
    """A document or annotation is outside the saved local library."""


class AnnotationConflictError(RuntimeError):
    """The request targets stale or unavailable immutable document content."""


class AnnotationKind(StrEnum):
    HIGHLIGHT = "highlight"
    NOTE = "note"
    BOOKMARK = "bookmark"


class AnnotationColor(StrEnum):
    AMBER = "amber"
    GREEN = "green"
    ROSE = "rose"
    BLUE = "blue"


@dataclass(frozen=True, slots=True)
class PdfRect:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        values = tuple(float(value) for value in (self.x0, self.y0, self.x1, self.y1))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("PDF rectangle coordinates must be finite")
        x0, y0, x1, y1 = values
        if x0 < 0 or y0 < 0:
            raise ValueError("PDF rectangle coordinates must be non-negative")
        if x1 <= x0 or y1 <= y0:
            raise ValueError("PDF rectangles must have positive area")
        object.__setattr__(self, "x0", x0)
        object.__setattr__(self, "y0", y0)
        object.__setattr__(self, "x1", x1)
        object.__setattr__(self, "y1", y1)


@dataclass(frozen=True, slots=True)
class ReadingPositionInput:
    document_content_sha256: str
    page_number: int
    page_count: int
    scale: float
    rotation: int

    def __post_init__(self) -> None:
        _validate_sha256(self.document_content_sha256)
        _validate_page_bounds(self.page_number, self.page_count)
        scale = float(self.scale)
        if not math.isfinite(scale) or not 0.5 <= scale <= 3:
            raise ValueError("scale must be finite and between 0.5 and 3")
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError("rotation must be 0, 90, 180, or 270")
        object.__setattr__(self, "scale", scale)

    @property
    def progress(self) -> float:
        return round(self.page_number / self.page_count * 100, 4)


@dataclass(frozen=True, slots=True)
class ReadingPosition:
    document_version_id: str
    document_content_sha256: str
    page_number: int
    page_count: int
    scale: float
    rotation: int
    progress: float
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AnnotationInput:
    document_content_sha256: str
    kind: AnnotationKind
    page_number: int
    page_count: int
    page_width: float
    page_height: float
    quote_text: str | None
    context_before: str | None
    context_after: str | None
    rects: tuple[PdfRect, ...]
    color: AnnotationColor
    note: str | None

    def __post_init__(self) -> None:
        _validate_sha256(self.document_content_sha256)
        kind = AnnotationKind(self.kind)
        color = AnnotationColor(self.color)
        _validate_page_bounds(self.page_number, self.page_count)
        width = float(self.page_width)
        height = float(self.page_height)
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
            or width > 100_000
            or height > 100_000
        ):
            raise ValueError("page dimensions must be finite and positive")
        rects = tuple(self.rects)
        if len(rects) > 64:
            raise ValueError("at most 64 PDF rectangles are allowed")
        if any(rect.x1 > width or rect.y1 > height for rect in rects):
            raise ValueError("PDF rectangles must remain inside page dimensions")
        _validate_optional_text("quote_text", self.quote_text, 10_000)
        _validate_optional_text("context_before", self.context_before, 2_000)
        _validate_optional_text("context_after", self.context_after, 2_000)
        _validate_optional_text("note", self.note, 20_000)
        if kind in {AnnotationKind.HIGHLIGHT, AnnotationKind.NOTE}:
            if self.quote_text is None or not self.quote_text.strip():
                raise ValueError("quote_text is required for highlights and notes")
            if not rects:
                raise ValueError("PDF rectangles are required for highlights and notes")
        if kind is AnnotationKind.NOTE and (self.note is None or not self.note.strip()):
            raise ValueError("note kind requires note text")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "color", color)
        object.__setattr__(self, "page_width", width)
        object.__setattr__(self, "page_height", height)
        object.__setattr__(self, "rects", rects)


@dataclass(frozen=True, slots=True)
class Annotation:
    id: str
    document_version_id: str
    document_content_sha256: str
    kind: AnnotationKind
    page_number: int
    page_width: float
    page_height: float
    quote_text: str | None
    context_before: str | None
    context_after: str | None
    rects: tuple[PdfRect, ...]
    color: AnnotationColor
    note: str | None
    created_at: datetime
    updated_at: datetime


class AnnotationRepository:
    """Own reading state and annotations without mutating extracted evidence."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def get_position(self, document_version_id: str) -> ReadingPosition | None:
        with self._sessions() as session:
            document, _ = self._saved_document(session, document_version_id)
            record = session.get(ReadingPositionRecord, document.id)
            if record is None:
                return None
            self._require_record_hash(record.document_content_sha256, document)
            return self._position_domain(record)

    def save_position(
        self,
        document_version_id: str,
        value: ReadingPositionInput,
    ) -> ReadingPosition:
        now = _require_aware(self._clock())
        with self._sessions() as session:
            document, canonical_work_id = self._saved_document(
                session,
                document_version_id,
                expected_hash=value.document_content_sha256,
                require_readable=True,
            )
            record = session.get(ReadingPositionRecord, document.id)
            if record is None:
                record = ReadingPositionRecord(
                    document_version_id=document.id,
                    document_content_sha256=document.content_sha256,
                    page_number=value.page_number,
                    page_count=value.page_count,
                    scale=value.scale,
                    rotation=value.rotation,
                    progress=value.progress,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                self._require_record_hash(record.document_content_sha256, document)
                changed = (
                    record.page_number != value.page_number
                    or record.page_count != value.page_count
                    or record.scale != value.scale
                    or record.rotation != value.rotation
                    or record.progress != value.progress
                )
                if changed:
                    record.page_number = value.page_number
                    record.page_count = value.page_count
                    record.scale = value.scale
                    record.rotation = value.rotation
                    record.progress = value.progress
                    record.updated_at = now
            self._sync_work_progress(
                session,
                work_id=canonical_work_id,
                progress=value.progress,
                now=now,
            )
            session.commit()
            return self._position_domain(record)

    def list(
        self,
        document_version_id: str,
        *,
        kind: AnnotationKind | None = None,
    ) -> tuple[Annotation, ...]:
        normalized_kind = AnnotationKind(kind) if kind is not None else None
        with self._sessions() as session:
            document, _ = self._saved_document(session, document_version_id)
            statement = select(AnnotationRecord).where(
                AnnotationRecord.document_version_id == document.id
            )
            if normalized_kind is not None:
                statement = statement.where(AnnotationRecord.kind == normalized_kind.value)
            records = tuple(
                session.scalars(
                    statement.order_by(
                        AnnotationRecord.page_number,
                        AnnotationRecord.created_at,
                        AnnotationRecord.id,
                    )
                )
            )
            for record in records:
                self._require_record_hash(record.document_content_sha256, document)
            return tuple(self._annotation_domain(record) for record in records)

    def create(
        self,
        document_version_id: str,
        value: AnnotationInput,
    ) -> Annotation:
        now = _require_aware(self._clock())
        with self._sessions() as session:
            document, _ = self._saved_document(
                session,
                document_version_id,
                expected_hash=value.document_content_sha256,
                require_readable=True,
            )
            record = AnnotationRecord(
                document_version_id=document.id,
                document_content_sha256=document.content_sha256,
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
            session.add(record)
            session.commit()
            return self._annotation_domain(record)

    def update(
        self,
        document_version_id: str,
        annotation_id: str,
        value: AnnotationInput,
    ) -> Annotation:
        now = _require_aware(self._clock())
        with self._sessions() as session:
            document, _ = self._saved_document(
                session,
                document_version_id,
                expected_hash=value.document_content_sha256,
                require_readable=True,
            )
            record = session.get(AnnotationRecord, annotation_id)
            if record is None or record.document_version_id != document.id:
                raise AnnotationNotFoundError(annotation_id)
            self._require_record_hash(record.document_content_sha256, document)
            record.kind = value.kind.value
            record.page_number = value.page_number
            record.page_width = value.page_width
            record.page_height = value.page_height
            record.quote_text = value.quote_text
            record.context_before = value.context_before
            record.context_after = value.context_after
            record.rects = [_rect_payload(rect) for rect in value.rects]
            record.color = value.color.value
            record.note = value.note
            record.updated_at = now
            session.commit()
            return self._annotation_domain(record)

    def delete(self, document_version_id: str, annotation_id: str) -> None:
        with self._sessions() as session:
            document, _ = self._saved_document(session, document_version_id)
            record = session.get(AnnotationRecord, annotation_id)
            if record is None or record.document_version_id != document.id:
                raise AnnotationNotFoundError(annotation_id)
            self._require_record_hash(record.document_content_sha256, document)
            session.delete(record)
            session.commit()

    @staticmethod
    def _saved_document(
        session: Session,
        document_version_id: str,
        *,
        expected_hash: str | None = None,
        require_readable: bool = False,
    ) -> tuple[DocumentVersionRecord, str]:
        document = session.get(DocumentVersionRecord, document_version_id)
        if document is None:
            raise AnnotationNotFoundError(document_version_id)
        canonical_work_id = _canonical_work_id(session, document.work_id)
        if session.get(LibraryEntryRecord, canonical_work_id) is None:
            raise AnnotationNotFoundError(document_version_id)
        if expected_hash is not None and document.content_sha256 != expected_hash:
            raise AnnotationConflictError("Document content hash no longer matches")
        if require_readable and (
            document.access_status not in _LEGAL_ACCESS
            or document.media_type.casefold() != _PDF_MEDIA_TYPE
            or document.relative_path is None
        ):
            raise AnnotationConflictError("Document content is not available for reading")
        return document, canonical_work_id

    @staticmethod
    def _require_record_hash(
        record_hash: str,
        document: DocumentVersionRecord,
    ) -> None:
        if record_hash != document.content_sha256:
            raise AnnotationConflictError("Stored annotation content hash is stale")

    @staticmethod
    def _sync_work_progress(
        session: Session,
        *,
        work_id: str,
        progress: float,
        now: datetime,
    ) -> None:
        state = session.get(LibraryStateRecord, work_id)
        if state is None:
            session.add(
                LibraryStateRecord(
                    work_id=work_id,
                    favorite=False,
                    feedback="neutral",
                    feedback_reasons=[],
                    pinned=False,
                    reading_progress=progress,
                    analysis_state="not_analyzed",
                    created_at=now,
                    updated_at=now,
                )
            )
        elif progress > state.reading_progress:
            state.reading_progress = progress
            state.updated_at = now

    @staticmethod
    def _position_domain(record: ReadingPositionRecord) -> ReadingPosition:
        return ReadingPosition(
            document_version_id=record.document_version_id,
            document_content_sha256=record.document_content_sha256,
            page_number=record.page_number,
            page_count=record.page_count,
            scale=record.scale,
            rotation=record.rotation,
            progress=record.progress,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
        )

    @staticmethod
    def _annotation_domain(record: AnnotationRecord) -> Annotation:
        return Annotation(
            id=record.id,
            document_version_id=record.document_version_id,
            document_content_sha256=record.document_content_sha256,
            kind=AnnotationKind(record.kind),
            page_number=record.page_number,
            page_width=record.page_width,
            page_height=record.page_height,
            quote_text=record.quote_text,
            context_before=record.context_before,
            context_after=record.context_after,
            rects=tuple(PdfRect(**rect) for rect in record.rects),
            color=AnnotationColor(record.color),
            note=record.note,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
        )


def _validate_sha256(value: str) -> None:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("document_content_sha256 must be a lowercase SHA-256 digest")


def _validate_page_bounds(page_number: int, page_count: int) -> None:
    if (
        isinstance(page_number, bool)
        or isinstance(page_count, bool)
        or not isinstance(page_number, int)
        or not isinstance(page_count, int)
        or page_number < 1
        or page_count < 1
        or page_number > page_count
    ):
        raise ValueError("page_number must be between 1 and page_count")


def _validate_optional_text(name: str, value: str | None, limit: int) -> None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{name} must contain at most {limit} characters")


def _rect_payload(rect: PdfRect) -> dict[str, float]:
    return {"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1}


def _canonical_work_id(session: Session, work_id: str) -> str:
    work = session.get(WorkRecord, work_id)
    if work is None:
        raise AnnotationNotFoundError(work_id)
    visited: set[str] = set()
    while work.status == "merged":
        if work.id in visited or work.merged_into_work_id is None:
            raise AnnotationConflictError("Invalid Work merge chain")
        visited.add(work.id)
        target = session.get(WorkRecord, work.merged_into_work_id)
        if target is None:
            raise AnnotationConflictError("Merged Work target is missing")
        work = target
    if work.status != "active":
        raise AnnotationConflictError("Work is not active")
    return work.id


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
