"""Persistence records owned by the local reading domain."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now


class LibraryEntryRecord(Base):
    """Explicit membership of a canonical Work in the user's local library."""

    __tablename__ = "library_entries"
    __table_args__ = (
        CheckConstraint(
            "added_via IN ('pdf', 'doi', 'bibtex', 'url', 'save')",
            name="ck_library_entry_added_via",
        ),
    )

    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_via: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


Index(
    "ix_library_entries_created_work",
    LibraryEntryRecord.created_at.desc(),
    LibraryEntryRecord.work_id,
)


class DocumentPageTextRecord(Base):
    """Current page-level native text plus separately auditable OCR fields."""

    __tablename__ = "document_page_texts"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="ck_document_page_number"),
        CheckConstraint(
            "page_width > 0 AND page_height > 0",
            name="ck_document_page_dimensions",
        ),
        CheckConstraint(
            "native_character_count >= 0",
            name="ck_document_page_character_count",
        ),
        CheckConstraint(
            "ocr_state IN ('not_needed', 'pending', 'complete', 'failed', 'unavailable')",
            name="ck_document_page_ocr_state",
        ),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_page_ocr_confidence",
        ),
    )

    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_width: Mapped[float] = mapped_column(Float)
    page_height: Mapped[float] = mapped_column(Float)
    native_text: Mapped[str] = mapped_column(Text)
    native_blocks: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    native_character_count: Mapped[int] = mapped_column(Integer)
    likely_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_state: Mapped[str] = mapped_column(String(24), default="not_needed", index=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_blocks: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_engine_version: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    ocr_model_bundle_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    extractor_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class ReadingPositionRecord(Base):
    """Last visible PDF state for one immutable document version."""

    __tablename__ = "reading_positions"
    __table_args__ = (
        CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_reading_position_sha256_length",
        ),
        CheckConstraint(
            "page_number >= 1 AND page_count >= 1 AND page_number <= page_count",
            name="ck_reading_position_page_bounds",
        ),
        CheckConstraint(
            "scale >= 0.5 AND scale <= 3",
            name="ck_reading_position_scale",
        ),
        CheckConstraint(
            "rotation IN (0, 90, 180, 270)",
            name="ck_reading_position_rotation",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_reading_position_progress",
        ),
    )

    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    scale: Mapped[float] = mapped_column(Float)
    rotation: Mapped[int] = mapped_column(Integer)
    progress: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class AnnotationRecord(Base):
    """User-owned evidence marker anchored to an immutable PDF hash."""

    __tablename__ = "annotations"
    __table_args__ = (
        CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_annotation_sha256_length",
        ),
        CheckConstraint(
            "kind IN ('highlight', 'note', 'bookmark')",
            name="ck_annotation_kind",
        ),
        CheckConstraint("page_number >= 1", name="ck_annotation_page_number"),
        CheckConstraint(
            "page_width > 0 AND page_height > 0",
            name="ck_annotation_page_dimensions",
        ),
        CheckConstraint(
            "color IN ('amber', 'green', 'rose', 'blue')",
            name="ck_annotation_color",
        ),
        CheckConstraint(
            "quote_text IS NULL OR length(quote_text) <= 10000",
            name="ck_annotation_quote_length",
        ),
        CheckConstraint(
            "context_before IS NULL OR length(context_before) <= 2000",
            name="ck_annotation_context_before_length",
        ),
        CheckConstraint(
            "context_after IS NULL OR length(context_after) <= 2000",
            name="ck_annotation_context_after_length",
        ),
        CheckConstraint(
            "note IS NULL OR length(note) <= 20000",
            name="ck_annotation_note_length",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        index=True,
    )
    document_content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    page_width: Mapped[float] = mapped_column(Float)
    page_height: Mapped[float] = mapped_column(Float)
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    rects: Mapped[list[dict[str, float]]] = mapped_column(JSON, default=list)
    color: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class AnnotationMigrationBatchRecord(Base):
    """Immutable preview identity for one old/new document pair."""

    __tablename__ = "annotation_migration_batches"
    __table_args__ = (
        UniqueConstraint("preview_key", name="uq_annotation_migration_preview_key"),
        CheckConstraint(
            "source_document_version_id != target_document_version_id",
            name="ck_annotation_migration_distinct_documents",
        ),
        CheckConstraint(
            "length(source_document_content_sha256) = 64",
            name="ck_annotation_migration_source_sha256_length",
        ),
        CheckConstraint(
            "length(target_document_content_sha256) = 64",
            name="ck_annotation_migration_target_sha256_length",
        ),
        CheckConstraint(
            "length(source_annotations_sha256) = 64",
            name="ck_annotation_migration_source_annotations_sha256_length",
        ),
        CheckConstraint(
            "length(target_extraction_sha256) = 64",
            name="ck_annotation_migration_target_extraction_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    preview_key: Mapped[str] = mapped_column(String(64), index=True)
    algorithm_version: Mapped[str] = mapped_column(String(80))
    source_document_version_id: Mapped[str] = mapped_column(String(36), index=True)
    source_document_content_sha256: Mapped[str] = mapped_column(String(64))
    target_document_version_id: Mapped[str] = mapped_column(String(36), index=True)
    target_document_content_sha256: Mapped[str] = mapped_column(String(64))
    source_annotations_sha256: Mapped[str] = mapped_column(String(64))
    target_extraction_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnnotationMigrationCandidateRecord(Base):
    """Immutable suggested target anchor for one source annotation snapshot."""

    __tablename__ = "annotation_migration_candidates"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "source_annotation_id",
            name="uq_annotation_migration_candidate_source",
        ),
        CheckConstraint("order_index >= 0", name="ck_annotation_migration_order"),
        CheckConstraint(
            "match_state IN ('matched', 'ambiguous', 'orphaned')",
            name="ck_annotation_migration_match_state",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low', 'none')",
            name="ck_annotation_migration_confidence",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_annotation_migration_score",
        ),
        CheckConstraint(
            "text_source IS NULL OR text_source IN ('native', 'ocr')",
            name="ck_annotation_migration_text_source",
        ),
        CheckConstraint(
            "length(source_annotation_sha256) = 64",
            name="ck_annotation_migration_annotation_sha256_length",
        ),
        CheckConstraint(
            "length(candidate_sha256) = 64",
            name="ck_annotation_migration_candidate_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("annotation_migration_batches.id", ondelete="CASCADE"),
        index=True,
    )
    source_annotation_id: Mapped[str] = mapped_column(String(36), index=True)
    source_annotation_sha256: Mapped[str] = mapped_column(String(64))
    source_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    order_index: Mapped[int] = mapped_column(Integer)
    match_state: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    target_page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_page_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_page_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_context_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_context_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_rects: Mapped[list[dict[str, float]]] = mapped_column(JSON, default=list)
    text_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alternatives: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    candidate_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnnotationMigrationDecisionRecord(Base):
    """Append-only user decision for one immutable migration candidate."""

    __tablename__ = "annotation_migration_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('accept', 'reject')",
            name="ck_annotation_migration_decision",
        ),
        CheckConstraint(
            "(decision = 'accept' AND target_annotation_id IS NOT NULL) OR "
            "(decision = 'reject' AND target_annotation_id IS NULL)",
            name="ck_annotation_migration_decision_target",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("annotation_migration_candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    decision: Mapped[str] = mapped_column(String(16))
    target_annotation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
