"""Normalized persistence records for related papers and knowledge resources."""

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


class RelatedResourceSetRecord(Base):
    __tablename__ = "related_resource_sets"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_related_resource_set_job"),
        CheckConstraint("intent_revision >= 1", name="ck_related_resource_set_revision"),
        CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_related_resource_set_document_hash",
        ),
        CheckConstraint(
            "status IN ('draft', 'confirmed', 'queued', 'running', 'partial', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_related_resource_set_status",
        ),
        CheckConstraint(
            "coverage >= 0 AND coverage <= 100",
            name="ck_related_resource_set_coverage",
        ),
        CheckConstraint(
            "include_academic_sources = 1 OR include_external_web = 1",
            name="ck_related_resource_set_target",
        ),
        CheckConstraint(
            "(include_external_web = 0 AND external_web_consent = 0) OR "
            "(include_external_web = 1 AND external_web_consent = 1)",
            name="ck_related_resource_set_external_consent",
        ),
        CheckConstraint(
            "(status = 'draft' AND confirmed_at IS NULL) OR "
            "(status != 'draft' AND confirmed_at IS NOT NULL)",
            name="ck_related_resource_set_confirmation",
        ),
        CheckConstraint(
            "(status IN ('draft', 'confirmed', 'queued', 'running') "
            "AND completed_at IS NULL) OR "
            "(status IN ('partial', 'succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL)",
            name="ck_related_resource_set_completion",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    document_content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    context_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_context_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    intent_revision: Mapped[int] = mapped_column(Integer, default=1)
    intent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    intent_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    include_academic_sources: Mapped[bool] = mapped_column(Boolean, default=True)
    include_external_web: Mapped[bool] = mapped_column(Boolean, default=False)
    external_web_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage: Mapped[float] = mapped_column(Float, default=0)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RelatedPaperCandidateRecord(Base):
    __tablename__ = "related_paper_candidates"
    __table_args__ = (
        UniqueConstraint(
            "set_id",
            "connector_id",
            "external_id",
            name="uq_related_paper_set_connector_external",
        ),
        UniqueConstraint(
            "set_id",
            "normalized_doi",
            name="uq_related_paper_set_doi",
        ),
        CheckConstraint("length(connector_id) >= 1", name="ck_related_paper_connector"),
        CheckConstraint("length(external_id) >= 1", name="ck_related_paper_external_id"),
        CheckConstraint("length(title) >= 1", name="ck_related_paper_title"),
        CheckConstraint("length(reason) >= 1", name="ck_related_paper_reason"),
        CheckConstraint(
            "normalized_doi IS NULL OR length(normalized_doi) >= 3",
            name="ck_related_paper_doi",
        ),
        CheckConstraint(
            "publication_year IS NULL OR (publication_year >= 1000 AND publication_year <= 9999)",
            name="ck_related_paper_year",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_related_paper_confidence",
        ),
        CheckConstraint(
            "verification_status IN ('verified', 'pending_verification', 'rejected')",
            name="ck_related_paper_verification",
        ),
        CheckConstraint(
            "linked_work_id IS NULL OR merge_review_id IS NULL",
            name="ck_related_paper_identity_result",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    set_id: Mapped[str] = mapped_column(
        ForeignKey("related_resource_sets.id", ondelete="CASCADE"),
    )
    connector_id: Mapped[str] = mapped_column(String(80), index=True)
    external_id: Mapped[str] = mapped_column(String(500))
    normalized_doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    match_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    verification_status: Mapped[str] = mapped_column(
        String(32),
        default="pending_verification",
        index=True,
    )
    linked_work_id: Mapped[str | None] = mapped_column(
        ForeignKey("works.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    merge_review_id: Mapped[str | None] = mapped_column(
        ForeignKey("merge_reviews.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class KnowledgeResourceRecord(Base):
    __tablename__ = "knowledge_resources"
    __table_args__ = (
        UniqueConstraint(
            "set_id",
            "canonical_url",
            name="uq_knowledge_resource_set_url",
        ),
        CheckConstraint("length(title) >= 1", name="ck_knowledge_resource_title"),
        CheckConstraint(
            "canonical_url LIKE 'https://%' AND final_url LIKE 'https://%'",
            name="ck_knowledge_resource_https",
        ),
        CheckConstraint("length(domain) >= 3", name="ck_knowledge_resource_domain"),
        CheckConstraint("length(reason) >= 1", name="ck_knowledge_resource_reason"),
        CheckConstraint(
            "resource_type IN ('official_docs', 'review', 'course', 'textbook', 'database')",
            name="ck_knowledge_resource_type",
        ),
        CheckConstraint(
            "verification_status IN ('verified', 'pending_verification', 'rejected')",
            name="ck_knowledge_resource_verification",
        ),
        CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_knowledge_resource_http_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    set_id: Mapped[str] = mapped_column(
        ForeignKey("related_resource_sets.id", ondelete="CASCADE"),
    )
    title: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    resource_type: Mapped[str] = mapped_column(String(24), index=True)
    reason: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32),
        default="pending_verification",
        index=True,
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class RelatedRunSourceRecord(Base):
    __tablename__ = "related_run_sources"
    __table_args__ = (
        UniqueConstraint(
            "set_id",
            "connector_id",
            name="uq_related_run_source_set_connector",
        ),
        CheckConstraint("length(connector_id) >= 1", name="ck_related_run_source_connector"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_related_run_source_status",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND displayed_count >= 0 AND displayed_count <= candidate_count",
            name="ck_related_run_source_counts",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_related_run_source_progress",
        ),
        CheckConstraint(
            "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled', 'skipped') "
            "AND completed_at IS NOT NULL)",
            name="ck_related_run_source_completion",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    set_id: Mapped[str] = mapped_column(
        ForeignKey("related_resource_sets.id", ondelete="CASCADE"),
    )
    connector_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    displayed_count: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
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


__all__ = [
    "KnowledgeResourceRecord",
    "RelatedPaperCandidateRecord",
    "RelatedResourceSetRecord",
    "RelatedRunSourceRecord",
]
