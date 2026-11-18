"""Persistence models for canonical works and their source/document versions."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    DDL,
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
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now


def new_uuid() -> str:
    return str(uuid4())


class WorkRecord(Base):
    """Stable canonical paper identity independent of any one connector."""

    __tablename__ = "works"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'merged')",
            name="ck_work_status",
        ),
        CheckConstraint(
            "(status = 'active' AND merged_into_work_id IS NULL) OR "
            "(status = 'merged' AND merged_into_work_id IS NOT NULL)",
            name="ck_work_merge_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    canonical_title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text, index=True)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    merged_into_work_id: Mapped[str | None] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


Index(
    "ix_works_status_doi",
    WorkRecord.status,
    WorkRecord.doi,
)
Index(
    "ix_works_status_normalized_title",
    WorkRecord.status,
    WorkRecord.normalized_title,
    WorkRecord.id,
)
Index(
    "ix_works_status_published_at",
    WorkRecord.status,
    WorkRecord.published_at,
    WorkRecord.id,
)


class SourceRecord(Base):
    """Current representation returned by one source connector."""

    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint(
            "connector_id",
            "external_id",
            name="uq_source_records_connector_external",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    connector_id: Mapped[str] = mapped_column(String(80), index=True)
    external_id: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text, index=True)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceRevisionRecord(Base):
    """Append-only snapshot of one distinct source response."""

    __tablename__ = "source_record_revisions"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "payload_sha256",
            name="uq_source_revision_payload",
        ),
        CheckConstraint(
            "revision_kind IN ('baseline', 'metadata_refresh', 'important_revision')",
            name="ck_source_revision_kind",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_source_revision_payload_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("source_records.id", ondelete="CASCADE"),
        index=True,
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    revision_kind: Mapped[str] = mapped_column(String(24), index=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    changed_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PaperUpdateRecord(Base):
    """Stateful notification derived from an immutable source revision."""

    __tablename__ = "paper_updates"
    __table_args__ = (
        UniqueConstraint("current_revision_id", name="uq_paper_update_revision"),
        CheckConstraint(
            "update_type IN ('metadata_refresh', 'important_revision')",
            name="ck_paper_update_type",
        ),
        CheckConstraint(
            "visibility IN ('internal', 'updates')",
            name="ck_paper_update_visibility",
        ),
        CheckConstraint(
            "state IN ('unread', 'read', 'dismissed')",
            name="ck_paper_update_state",
        ),
        CheckConstraint(
            "(update_type = 'metadata_refresh' AND visibility = 'internal') OR "
            "(update_type = 'important_revision' AND visibility = 'updates')",
            name="ck_paper_update_visibility_type",
        ),
        CheckConstraint(
            "previous_revision_id != current_revision_id",
            name="ck_paper_update_distinct_revisions",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("source_records.id", ondelete="CASCADE"),
        index=True,
    )
    previous_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_record_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    current_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_record_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    update_type: Mapped[str] = mapped_column(String(24), index=True)
    visibility: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(16), default="unread", index=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentVersionRecord(Base):
    """Content-addressed paper file or extracted text version."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("work_id", "content_sha256", name="uq_document_versions_hash"),
        CheckConstraint("length(content_sha256) = 64", name="ck_document_sha256_length"),
        CheckConstraint(
            "access_status IN ('open', 'user_provided', 'restricted', 'unknown')",
            name="ck_document_access_status",
        ),
        CheckConstraint(
            "completeness >= 0 AND completeness <= 100",
            name="ck_document_completeness",
        ),
        CheckConstraint("byte_size >= 0", name="ck_document_byte_size"),
        CheckConstraint(
            "acquisition_kind IN ('source', 'manual_import', 'download')",
            name="ck_document_acquisition_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    source_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_records.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    access_status: Mapped[str] = mapped_column(String(24))
    media_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    completeness: Mapped[int] = mapped_column(Integer, default=0)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    extraction_status: Mapped[str] = mapped_column(String(24), default="pending")
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquisition_kind: Mapped[str] = mapped_column(String(24), default="source")
    acquisition_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


Index(
    "ix_document_versions_work_representative",
    DocumentVersionRecord.work_id,
    DocumentVersionRecord.source_updated_at.desc(),
    DocumentVersionRecord.completeness.desc(),
    DocumentVersionRecord.created_at.desc(),
    DocumentVersionRecord.id,
)


class MergeReviewRecord(Base):
    """Explicit review queue entry for an ambiguous cross-source identity match."""

    __tablename__ = "merge_reviews"
    __table_args__ = (
        UniqueConstraint(
            "candidate_work_id",
            "matched_work_id",
            name="uq_merge_reviews_candidate_match",
        ),
        CheckConstraint(
            "candidate_work_id != matched_work_id",
            name="ck_merge_review_distinct_works",
        ),
        CheckConstraint(
            "similarity >= 0 AND similarity <= 1",
            name="ck_merge_review_similarity",
        ),
        CheckConstraint(
            "status IN ('pending', 'merged', 'kept_separate', 'superseded')",
            name="ck_merge_review_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    matched_work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    similarity: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KeywordSetVersionRecord(Base):
    """Append-only Agent/manual keyword proposal; confirmation is a separate fact."""

    __tablename__ = "keyword_set_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("keyword_set_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    research_intent: Mapped[str] = mapped_column(Text)
    required_groups: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    boost_groups: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    excluded_groups: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    change_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generator_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KeywordConfirmationRecord(Base):
    """One-way confirmation of a keyword version; absence means it cannot run."""

    __tablename__ = "keyword_confirmations"

    keyword_version_id: Mapped[str] = mapped_column(
        ForeignKey("keyword_set_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveryPlanRecord(Base):
    """Mutable watch-plan configuration; runs will bind immutable snapshots."""

    __tablename__ = "discovery_plans"
    __table_args__ = (
        CheckConstraint(
            "(date_mode = 'fixed' AND start_month IS NOT NULL AND end_month IS NOT NULL "
            "AND rolling_months IS NULL) OR "
            "(date_mode = 'rolling' AND start_month IS NULL AND end_month IS NULL "
            "AND rolling_months >= 1)",
            name="ck_discovery_plan_date_mode",
        ),
        CheckConstraint(
            "schedule_frequency IN ('manual', 'daily', 'weekly', 'monthly')",
            name="ck_discovery_plan_schedule_frequency",
        ),
        CheckConstraint(
            "weekly_day >= 0 AND weekly_day <= 6",
            name="ck_discovery_plan_weekly_day",
        ),
        CheckConstraint(
            "monthly_day >= 1 AND monthly_day <= 28",
            name="ck_discovery_plan_monthly_day",
        ),
        CheckConstraint(
            "priority_limit >= 1 AND candidate_limit >= priority_limit",
            name="ck_discovery_plan_quotas",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160))
    research_intent: Mapped[str] = mapped_column(Text)
    keyword_version_id: Mapped[str] = mapped_column(
        ForeignKey("keyword_set_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    date_mode: Mapped[str] = mapped_column(String(16))
    start_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    end_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    rolling_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    sources: Mapped[list[str]] = mapped_column(JSON)
    source_filters: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSON, default=dict)
    schedule_frequency: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    schedule_local_time: Mapped[str] = mapped_column(String(5), default="08:00")
    weekly_day: Mapped[int] = mapped_column(Integer, default=0)
    monthly_day: Mapped[int] = mapped_column(Integer, default=1)
    priority_limit: Mapped[int] = mapped_column(Integer, default=20)
    candidate_limit: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(default=True, index=True)
    schedule_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PlanCatchupRecord(Base):
    """A missed schedule occurrence that never runs without explicit confirmation."""

    __tablename__ = "plan_catchups"
    __table_args__ = (
        UniqueConstraint("plan_id", "scheduled_for", name="uq_plan_catchup_occurrence"),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'dismissed')",
            name="ck_plan_catchup_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("discovery_plans.id", ondelete="CASCADE"),
        index=True,
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanRunRecord(Base):
    """Immutable discovery configuration snapshot with mutable execution state."""

    __tablename__ = "plan_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial_success', "
            "'failed', 'cancelled')",
            name="ck_plan_run_status",
        ),
        CheckConstraint(
            "total_batches >= 1 AND succeeded_batches >= 0 AND failed_batches >= 0 "
            "AND cancelled_batches >= 0 AND "
            "succeeded_batches + failed_batches + cancelled_batches <= total_batches",
            name="ck_plan_run_batch_counts",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("discovery_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    keyword_version_id: Mapped[str] = mapped_column(
        ForeignKey("keyword_set_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    months: Mapped[list[str]] = mapped_column(JSON)
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    keyword_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    connector_versions: Mapped[dict[str, str]] = mapped_column(JSON)
    total_batches: Mapped[int] = mapped_column(Integer)
    succeeded_batches: Mapped[int] = mapped_column(Integer, default=0)
    failed_batches: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_batches: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunBatchRecord(Base):
    """One resumable month-by-source unit inside a discovery run."""

    __tablename__ = "run_batches"
    __table_args__ = (
        UniqueConstraint("run_id", "month", "connector_id", name="uq_run_batch_scope"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_run_batch_status",
        ),
        CheckConstraint(
            "fetched_count >= 0 AND accepted_count >= 0 AND ingested_count >= 0 "
            "AND attempt_count >= 0",
            name="ck_run_batch_counts",
        ),
        CheckConstraint(
            "new_work_count >= 0 AND update_count >= 0 AND duplicate_count >= 0 "
            "AND scanned_count >= 0",
            name="ck_run_batch_discovery_counts",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("plan_runs.id", ondelete="CASCADE"),
        index=True,
    )
    month: Mapped[str] = mapped_column(String(7), index=True)
    connector_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    query_fingerprint: Mapped[str] = mapped_column(String(64))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    ingested_count: Mapped[int] = mapped_column(Integer, default=0)
    new_work_count: Mapped[int] = mapped_column(Integer, default=0)
    update_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_after_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunCandidateRecord(Base):
    """Auditable source-record membership in one immutable discovery run."""

    __tablename__ = "run_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", "source_record_id", name="uq_run_candidate_source"),
        CheckConstraint(
            "match_kind IN ('new', 'existing_source', 'exact_doi', "
            "'auto_similarity', 'review_required')",
            name="ck_run_candidate_match_kind",
        ),
        CheckConstraint(
            "discovery_kind IN ('new_work', 'existing_work_new_source', 'unchanged', "
            "'metadata_refresh', 'important_revision')",
            name="ck_run_candidate_discovery_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("plan_runs.id", ondelete="CASCADE"),
        index=True,
    )
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("run_batches.id", ondelete="CASCADE"),
        index=True,
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("source_records.id", ondelete="RESTRICT"),
        index=True,
    )
    match_kind: Mapped[str] = mapped_column(String(24))
    discovery_kind: Mapped[str] = mapped_column(String(32), default="unchanged", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MetadataObservationRecord(Base):
    """Append-only normalized response from one official metadata enricher."""

    __tablename__ = "metadata_observations"
    __table_args__ = (
        UniqueConstraint(
            "work_id",
            "enricher_id",
            "observation_sha256",
            name="uq_metadata_observation_identity",
        ),
        CheckConstraint(
            "status IN ('observed', 'not_found', 'not_applicable', 'disabled')",
            name="ck_metadata_observation_status",
        ),
        CheckConstraint(
            "length(observation_sha256) = 64",
            name="ck_metadata_observation_sha256_length",
        ),
        CheckConstraint(
            "response_sha256 IS NULL OR length(response_sha256) = 64",
            name="ck_metadata_response_sha256_length",
        ),
        CheckConstraint(
            "(status = 'observed' AND external_id IS NOT NULL "
            "AND matched_by IS NOT NULL AND response_sha256 IS NOT NULL) OR "
            "(status != 'observed' AND response_sha256 IS NULL)",
            name="ck_metadata_observation_payload",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    enricher_id: Mapped[str] = mapped_column(String(80), index=True)
    enricher_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), index=True)
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    matched_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observation_sha256: Mapped[str] = mapped_column(String(64))
    rate_limit: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScoreSnapshotRecord(Base):
    """Immutable, reproducible score result and its exact evidence policy."""

    __tablename__ = "score_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "work_id",
            "context_key",
            "score_type",
            "snapshot_sha256",
            name="uq_score_snapshot_identity",
        ),
        CheckConstraint(
            "score_type IN ('relevance', 'quality', 'freshness', 'preference', 'composite')",
            name="ck_score_snapshot_type",
        ),
        CheckConstraint("length(context_key) > 0", name="ck_score_snapshot_context"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_score_snapshot_score"),
        CheckConstraint(
            "coverage >= 0 AND coverage <= 100",
            name="ck_score_snapshot_coverage",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_score_snapshot_confidence",
        ),
        CheckConstraint(
            "length(snapshot_sha256) = 64",
            name="ck_score_snapshot_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    context_key: Mapped[str] = mapped_column(String(255), index=True)
    score_type: Mapped[str] = mapped_column(String(24), index=True)
    rule_version: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    weights: Mapped[dict[str, float]] = mapped_column(JSON)
    missing_policy: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_observation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    hard_constraints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RecommendationSetRecord(Base):
    """Immutable ranked result set produced by one scoring pass for a run."""

    __tablename__ = "recommendation_sets"
    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_recommendation_set_run_version"),
        UniqueConstraint("run_id", "set_sha256", name="uq_recommendation_set_run_sha256"),
        CheckConstraint("version >= 1", name="ck_recommendation_set_version"),
        CheckConstraint(
            "requested_limit >= 1 AND requested_limit <= 50",
            name="ck_recommendation_set_requested_limit",
        ),
        CheckConstraint(
            "candidate_cap >= requested_limit AND candidate_cap <= 50",
            name="ck_recommendation_set_candidate_cap",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND recommendation_count >= 0 "
            "AND recommendation_count <= candidate_count",
            name="ck_recommendation_set_counts",
        ),
        CheckConstraint(
            "exploration_target >= 0 AND exploration_count >= 0 "
            "AND exploration_count <= exploration_target "
            "AND exploration_target <= recommendation_count",
            name="ck_recommendation_set_exploration",
        ),
        CheckConstraint(
            "length(ranking_rule_version) > 0",
            name="ck_recommendation_set_rule_version",
        ),
        CheckConstraint(
            "length(set_sha256) = 64",
            name="ck_recommendation_set_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("plan_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    keyword_version_id: Mapped[str] = mapped_column(
        ForeignKey("keyword_set_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    ranking_rule_version: Mapped[str] = mapped_column(String(80))
    fixed_historical_months: Mapped[bool] = mapped_column(Boolean)
    requested_limit: Mapped[int] = mapped_column(Integer)
    candidate_cap: Mapped[int] = mapped_column(Integer)
    candidate_count: Mapped[int] = mapped_column(Integer)
    recommendation_count: Mapped[int] = mapped_column(Integer)
    exploration_target: Mapped[int] = mapped_column(Integer)
    exploration_count: Mapped[int] = mapped_column(Integer)
    degradation_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    set_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RecommendationItemRecord(Base):
    """One scored identity and its exact evidence inside an immutable result set."""

    __tablename__ = "recommendation_items"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_set_id",
            "scored_work_id",
            name="uq_recommendation_item_set_work",
        ),
        UniqueConstraint(
            "recommendation_set_id",
            "default_position",
            name="uq_recommendation_item_set_position",
        ),
        CheckConstraint(
            "(selected = 1 AND historical_match = 0 "
            "AND default_position IS NOT NULL "
            "AND channel IN ('exploitation', 'exploration')) OR "
            "(selected = 0 AND default_position IS NULL AND channel IS NULL)",
            name="ck_recommendation_item_selection",
        ),
        CheckConstraint(
            "default_position IS NULL OR default_position >= 1",
            name="ck_recommendation_item_position",
        ),
        CheckConstraint(
            "length(item_sha256) = 64",
            name="ck_recommendation_item_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    recommendation_set_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_sets.id", ondelete="RESTRICT"),
        index=True,
    )
    scored_work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    default_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean)
    historical_match: Mapped[bool] = mapped_column(Boolean, index=True)
    eligible_by_default: Mapped[bool] = mapped_column(Boolean, index=True)
    channel: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ineligibility_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    exploration_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    relevance_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    quality_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    freshness_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    preference_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    composite_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("score_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    ranking_evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    item_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScoreRuleVersionRecord(Base):
    """Immutable user-authored scoring formula version."""

    __tablename__ = "score_rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_kind", "revision", name="uq_score_rule_kind_revision"),
        UniqueConstraint("rule_kind", "rule_sha256", name="uq_score_rule_kind_sha256"),
        CheckConstraint("rule_kind IN ('quality')", name="ck_score_rule_kind"),
        CheckConstraint("revision >= 1", name="ck_score_rule_revision"),
        CheckConstraint("length(name) > 0", name="ck_score_rule_name"),
        CheckConstraint("length(rule_sha256) = 64", name="ck_score_rule_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rule_kind: Mapped[str] = mapped_column(String(24), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    dimensions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    rule_sha256: Mapped[str] = mapped_column(String(64), index=True)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("score_rule_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScoreRuleSelectionRecord(Base):
    """Mutable pointer to the active immutable scoring formula."""

    __tablename__ = "score_rule_selections"
    __table_args__ = (
        CheckConstraint("rule_kind IN ('quality')", name="ck_score_rule_selection_kind"),
    )

    rule_kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("score_rule_versions.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RatingCatalogRecord(Base):
    """Immutable metadata for one user-provided venue rating file."""

    __tablename__ = "rating_catalogs"
    __table_args__ = (
        UniqueConstraint("kind", "file_sha256", name="uq_rating_catalog_kind_file"),
        CheckConstraint(
            "kind IN ('jcr', 'cas', 'emerging')",
            name="ck_rating_catalog_kind",
        ),
        CheckConstraint("length(file_sha256) = 64", name="ck_rating_catalog_sha256_length"),
        CheckConstraint("row_count >= 1", name="ck_rating_catalog_row_count"),
        CheckConstraint("rights_confirmed = 1", name="ck_rating_catalog_rights"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    catalog_name: Mapped[str] = mapped_column(String(160))
    catalog_version: Mapped[str] = mapped_column(String(80))
    source_note: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    rights_confirmed: Mapped[bool] = mapped_column(Boolean)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VenueRatingRecord(Base):
    """Immutable row from one imported venue rating catalog."""

    __tablename__ = "venue_ratings"
    __table_args__ = (
        UniqueConstraint("catalog_id", "venue_key", name="uq_venue_rating_catalog_key"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_venue_rating_score"),
        CheckConstraint("row_number >= 2", name="ck_venue_rating_row_number"),
        CheckConstraint("length(venue_key) > 0", name="ck_venue_rating_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    catalog_id: Mapped[str] = mapped_column(
        ForeignKey("rating_catalogs.id", ondelete="RESTRICT"),
        index=True,
    )
    venue_key: Mapped[str] = mapped_column(Text, index=True)
    venue_name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    rating_label: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float)
    identifiers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    row_number: Mapped[int] = mapped_column(Integer)


class RatingCatalogSelectionRecord(Base):
    """Mutable pointer selecting the active immutable catalog for one kind."""

    __tablename__ = "rating_catalog_selections"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('jcr', 'cas', 'emerging')",
            name="ck_rating_catalog_selection_kind",
        ),
    )

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    catalog_id: Mapped[str] = mapped_column(
        ForeignKey("rating_catalogs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LibraryStateRecord(Base):
    """Independent explicit reading, feedback, and analysis state for one Work."""

    __tablename__ = "library_states"
    __table_args__ = (
        CheckConstraint(
            "feedback IN ('neutral', 'like', 'dislike')",
            name="ck_library_state_feedback",
        ),
        CheckConstraint(
            "reading_progress >= 0 AND reading_progress <= 100",
            name="ck_library_state_progress",
        ),
        CheckConstraint(
            "analysis_state IN ('not_analyzed', 'available', 'failed')",
            name="ck_library_state_analysis",
        ),
    )

    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[str] = mapped_column(String(16), default="neutral")
    feedback_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    reading_progress: Mapped[float] = mapped_column(Float, default=0)
    analysis_state: Mapped[str] = mapped_column(String(24), default="not_analyzed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ImplicitPreferenceEventRecord(Base):
    """Whitelisted raw in-app preference event retained for at most 180 days."""

    __tablename__ = "implicit_preference_events"
    __table_args__ = (
        CheckConstraint("length(event_key) > 0", name="ck_preference_event_key"),
        CheckConstraint(
            "event_type IN ('list_open', 'detail_open', 'pdf_open', "
            "'reading_duration', 'reading_completed', 'analysis_report_generated')",
            name="ck_preference_event_type",
        ),
        CheckConstraint(
            "(event_type = 'reading_duration' AND duration_bucket IN "
            "('lt_30s', '30s_2m', '2m_10m', 'gte_10m')) OR "
            "(event_type != 'reading_duration' AND duration_bucket IS NULL)",
            name="ck_preference_event_duration",
        ),
        CheckConstraint("weight > 0 AND weight <= 10", name="ck_preference_event_weight"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    duration_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    weight: Mapped[float] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PreferenceAggregateRecord(Base):
    """Irreversible per-Work weight produced after raw-event retention expires."""

    __tablename__ = "preference_aggregates"
    __table_args__ = (
        CheckConstraint("total_weight >= 0", name="ck_preference_aggregate_weight"),
    )

    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    total_weight: Mapped[float] = mapped_column(Float, default=0)
    aggregated_through: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class InterestedAuthorRecord(Base):
    """Mutable explicit author interest without inferred system-level behavior."""

    __tablename__ = "interested_authors"
    __table_args__ = (
        CheckConstraint("length(author_key) > 0", name="ck_interested_author_key"),
        CheckConstraint("length(display_name) > 0", name="ck_interested_author_name"),
    )

    author_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


for trigger in (
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_keyword_versions_no_update
        BEFORE UPDATE ON keyword_set_versions
        BEGIN SELECT RAISE(ABORT, 'KeywordSetVersion is immutable'); END
        """
    ),
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_keyword_versions_no_delete
        BEFORE DELETE ON keyword_set_versions
        BEGIN SELECT RAISE(ABORT, 'KeywordSetVersion is immutable'); END
        """
    ),
):
    event.listen(
        KeywordSetVersionRecord.__table__,
        "after_create",
        trigger.execute_if(dialect="sqlite"),
    )

for operation in ("UPDATE", "DELETE"):
    trigger = DDL(  # type: ignore[no-untyped-call]
        f"""
        CREATE TRIGGER trg_source_record_revisions_no_{operation.casefold()}
        BEFORE {operation} ON source_record_revisions
        BEGIN SELECT RAISE(ABORT, 'SourceRevision is immutable'); END
        """
    )
    event.listen(
        SourceRevisionRecord.__table__,
        "after_create",
        trigger.execute_if(dialect="sqlite"),
    )

for trigger in (
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_keyword_confirmations_no_update
        BEFORE UPDATE ON keyword_confirmations
        BEGIN SELECT RAISE(ABORT, 'Keyword confirmation is immutable'); END
        """
    ),
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_keyword_confirmations_no_delete
        BEFORE DELETE ON keyword_confirmations
        BEGIN SELECT RAISE(ABORT, 'Keyword confirmation is immutable'); END
        """
    ),
):
    event.listen(
        KeywordConfirmationRecord.__table__,
        "after_create",
        trigger.execute_if(dialect="sqlite"),
    )

for trigger in (
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_score_snapshots_no_update
        BEFORE UPDATE ON score_snapshots
        BEGIN SELECT RAISE(ABORT, 'ScoreSnapshot is immutable'); END
        """
    ),
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_score_snapshots_no_delete
        BEFORE DELETE ON score_snapshots
        BEGIN SELECT RAISE(ABORT, 'ScoreSnapshot is immutable'); END
        """
    ),
):
    event.listen(
        ScoreSnapshotRecord.__table__,
        "after_create",
        trigger.execute_if(dialect="sqlite"),
    )

for table, table_name, label in (
    (RecommendationSetRecord.__table__, "recommendation_sets", "RecommendationSet"),
    (RecommendationItemRecord.__table__, "recommendation_items", "RecommendationItem"),
):
    for operation in ("UPDATE", "DELETE"):
        trigger = DDL(  # type: ignore[no-untyped-call]
            f"""
            CREATE TRIGGER trg_{table_name}_no_{operation.casefold()}
            BEFORE {operation} ON {table_name}
            BEGIN SELECT RAISE(ABORT, '{label} is immutable'); END
            """
        )
        event.listen(table, "after_create", trigger.execute_if(dialect="sqlite"))

for table, table_name, label in (
    (RatingCatalogRecord.__table__, "rating_catalogs", "RatingCatalog"),
    (VenueRatingRecord.__table__, "venue_ratings", "VenueRating"),
):
    for operation in ("UPDATE", "DELETE"):
        trigger = DDL(  # type: ignore[no-untyped-call]
            f"""
            CREATE TRIGGER trg_{table_name}_no_{operation.casefold()}
            BEFORE {operation} ON {table_name}
            BEGIN SELECT RAISE(ABORT, '{label} is immutable'); END
            """
        )
        event.listen(table, "after_create", trigger.execute_if(dialect="sqlite"))

for operation in ("UPDATE", "DELETE"):
    trigger = DDL(  # type: ignore[no-untyped-call]
        f"""
        CREATE TRIGGER trg_score_rule_versions_no_{operation.casefold()}
        BEFORE {operation} ON score_rule_versions
        BEGIN SELECT RAISE(ABORT, 'ScoreRuleVersion is immutable'); END
        """
    )
    event.listen(
        ScoreRuleVersionRecord.__table__,
        "after_create",
        trigger.execute_if(dialect="sqlite"),
    )

event.listen(
    ImplicitPreferenceEventRecord.__table__,
    "after_create",
    DDL(  # type: ignore[no-untyped-call]
        """
        CREATE TRIGGER trg_implicit_preference_events_no_update
        BEFORE UPDATE ON implicit_preference_events
        WHEN NEW.id IS NOT OLD.id
          OR NEW.event_key IS NOT OLD.event_key
          OR NEW.event_type IS NOT OLD.event_type
          OR NEW.duration_bucket IS NOT OLD.duration_bucket
          OR NEW.weight IS NOT OLD.weight
          OR NEW.occurred_at IS NOT OLD.occurred_at
          OR NEW.created_at IS NOT OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'ImplicitPreferenceEvent is immutable'); END
        """
    ).execute_if(dialect="sqlite"),
)
