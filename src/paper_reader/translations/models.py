"""SQLAlchemy records for versioned translation artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now


def _new_id() -> str:
    return str(uuid4())


class TranslationArtifactRecord(Base):
    __tablename__ = "translation_artifacts"
    __table_args__ = (
        UniqueConstraint("work_id", "version", name="uq_translation_artifact_work_version"),
        UniqueConstraint("task_id", name="uq_translation_artifact_task"),
        UniqueConstraint("job_id", name="uq_translation_artifact_job"),
        CheckConstraint("version >= 1", name="ck_translation_artifact_version"),
        CheckConstraint(
            "source_language IN ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'de', 'fr') "
            "AND target_language IN ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'de', 'fr')",
            name="ck_translation_artifact_languages",
        ),
        CheckConstraint(
            "source_language != target_language",
            name="ck_translation_artifact_distinct_languages",
        ),
        CheckConstraint(
            "scope IN ('selection', 'page', 'section', 'full_document')",
            name="ck_translation_artifact_scope",
        ),
        CheckConstraint(
            "status IN ('draft', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_translation_artifact_status",
        ),
        CheckConstraint(
            "evidence_level IN ('native_text', 'ocr_text', 'mixed_text')",
            name="ck_translation_artifact_evidence_level",
        ),
        CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_translation_artifact_document_hash",
        ),
        CheckConstraint(
            "length(prompt_version) >= 1",
            name="ck_translation_artifact_prompt_version",
        ),
        CheckConstraint(
            "(status IN ('draft', 'running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL)",
            name="ck_translation_artifact_completion",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    document_content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    context_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_context_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="RESTRICT"),
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
    )
    source_language: Mapped[str] = mapped_column(String(16))
    target_language: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    evidence_level: Mapped[str] = mapped_column(String(24))
    prompt_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TranslationSegmentRecord(Base):
    __tablename__ = "translation_segments"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "segment_index",
            name="uq_translation_segment_artifact_index",
        ),
        CheckConstraint("segment_index >= 1", name="ck_translation_segment_index"),
        CheckConstraint("page_number >= 1", name="ck_translation_segment_page"),
        CheckConstraint(
            "length(source_sha256) = 64",
            name="ck_translation_segment_source_hash",
        ),
        CheckConstraint(
            "source_kind IN ('native', 'ocr')",
            name="ck_translation_segment_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'native' AND ocr_confidence IS NULL) OR "
            "(source_kind = 'ocr' AND ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_translation_segment_ocr_confidence",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'stale', 'failed')",
            name="ck_translation_segment_status",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'stale') OR translated_text IS NOT NULL",
            name="ck_translation_segment_translated_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("translation_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    segment_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    block_numbers: Mapped[list[int]] = mapped_column(JSON, default=list)
    source_text: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    protected_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16))
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class TranslationTermRecord(Base):
    __tablename__ = "translation_terms"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "normalized_source_term",
            name="uq_translation_term_artifact_source",
        ),
        CheckConstraint(
            "length(normalized_source_term) >= 1 AND length(source_term) >= 1",
            name="ck_translation_term_source",
        ),
        CheckConstraint(
            "origin IN ('local_extraction', 'user', 'agent')",
            name="ck_translation_term_origin",
        ),
        CheckConstraint(
            "locked = 0 OR target_term IS NOT NULL",
            name="ck_translation_term_locked_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("translation_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    normalized_source_term: Mapped[str] = mapped_column(String(500))
    source_term: Mapped[str] = mapped_column(String(500))
    target_term: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    origin: Mapped[str] = mapped_column(String(24))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class TranslationExportRecord(Base):
    __tablename__ = "translation_exports"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "format",
            "content_sha256",
            name="uq_translation_export_artifact_format_hash",
        ),
        CheckConstraint(
            "format IN ('markdown', 'html', 'print_pdf')",
            name="ck_translation_export_format",
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_translation_export_content_hash",
        ),
        CheckConstraint(
            "length(relative_path) >= 1",
            name="ck_translation_export_relative_path",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("translation_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    format: Mapped[str] = mapped_column(String(16))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


__all__ = [
    "TranslationArtifactRecord",
    "TranslationExportRecord",
    "TranslationSegmentRecord",
    "TranslationTermRecord",
]
