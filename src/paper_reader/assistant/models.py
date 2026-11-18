"""SQLAlchemy records for durable, evidence-bound Assistant history."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
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


class AssistantSessionRecord(Base):
    __tablename__ = "assistant_sessions"
    __table_args__ = (UniqueConstraint("work_id", name="uq_assistant_session_work"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AssistantContextSnapshotRecord(Base):
    __tablename__ = "assistant_context_snapshots"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('selection', 'current_page', 'current_section', "
            "'full_document', 'annotations')",
            name="ck_assistant_context_scope",
        ),
        CheckConstraint(
            "(scope = 'selection' AND selection IS NOT NULL) OR "
            "(scope != 'selection' AND selection IS NULL)",
            name="ck_assistant_context_selection_scope",
        ),
        CheckConstraint(
            "length(document_content_sha256) = 64 AND length(evidence_sha256) = 64 "
            "AND length(snapshot_sha256) = 64",
            name="ck_assistant_context_hashes",
        ),
        UniqueConstraint("snapshot_sha256", name="uq_assistant_context_snapshot_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    document_content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(24), index=True)
    page_numbers: Mapped[list[int]] = mapped_column(JSON, default=list)
    selection: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    annotation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    section_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_sha256: Mapped[str] = mapped_column(String(64), index=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantTaskRecord(Base):
    __tablename__ = "assistant_tasks"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('ask', 'explain', 'translate', 'related')",
            name="ck_assistant_task_mode",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_assistant_task_status",
        ),
        CheckConstraint(
            "network_policy IN ('local_only', 'external_research')",
            name="ck_assistant_task_network_policy",
        ),
        CheckConstraint(
            "(mode NOT IN ('ask', 'explain')) OR network_policy = 'local_only'",
            name="ck_assistant_task_quick_local_only",
        ),
        CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_assistant_task_reasoning",
        ),
        CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_assistant_task_service_tier",
        ),
        CheckConstraint(
            "output_language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_assistant_task_output_language",
        ),
        CheckConstraint("length(command_sha256) = 64", name="ck_assistant_task_command_hash"),
        UniqueConstraint("idempotency_key", name="uq_assistant_task_idempotency"),
        UniqueConstraint("job_id", name="uq_assistant_task_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    context_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_context_snapshots.id", ondelete="RESTRICT"),
        index=True,
    )
    retry_of_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True, default="queued")
    network_policy: Mapped[str] = mapped_column(String(24))
    backend_id: Mapped[str] = mapped_column(String(80))
    requested_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reasoning_effort: Mapped[str] = mapped_column(String(16))
    service_tier: Mapped[str] = mapped_column(String(16))
    output_language: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    command_sha256: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AssistantAgentRunRecord(Base):
    __tablename__ = "assistant_agent_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt", name="uq_assistant_agent_run_attempt"),
        CheckConstraint("attempt >= 1", name="ck_assistant_agent_run_attempt"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_assistant_agent_run_status",
        ),
        CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_assistant_agent_run_reasoning",
        ),
        CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_assistant_agent_run_service_tier",
        ),
        CheckConstraint(
            "repair_count >= 0 AND repair_count <= 1",
            name="ck_assistant_agent_run_repair_count",
        ),
        CheckConstraint(
            "(request_sha256 IS NULL OR length(request_sha256) = 64) AND "
            "(prompt_sha256 IS NULL OR length(prompt_sha256) = 64) AND "
            "(raw_output_sha256 IS NULL OR length(raw_output_sha256) = 64)",
            name="ck_assistant_agent_run_hashes",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    backend_id: Mapped[str] = mapped_column(String(80))
    requested_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reasoning_effort: Mapped[str] = mapped_column(String(16))
    service_tier: Mapped[str] = mapped_column(String(16))
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantMessageRecord(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_assistant_message_sequence"),
        CheckConstraint("sequence >= 1", name="ck_assistant_message_sequence"),
        CheckConstraint(
            "role IN ('user', 'assistant', 'system_diagnostic')",
            name="ck_assistant_message_role",
        ),
        CheckConstraint(
            "status IN ('committed', 'complete', 'failed', 'cancelled')",
            name="ck_assistant_message_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="RESTRICT"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        index=True,
    )
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("assistant_messages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantFragmentRecord(Base):
    __tablename__ = "assistant_fragments"
    __table_args__ = (
        CheckConstraint("sequence >= 0", name="ck_assistant_fragment_sequence"),
        CheckConstraint(
            "kind IN ('text', 'reasoning', 'diagnostic')",
            name="ck_assistant_fragment_kind",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_agent_runs.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssistantCheckpointRecord(Base):
    __tablename__ = "assistant_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "through_sequence",
            name="uq_assistant_checkpoint_through_sequence",
        ),
        CheckConstraint(
            "source_start_sequence >= 1 AND through_sequence >= source_start_sequence",
            name="ck_assistant_checkpoint_range",
        ),
        CheckConstraint(
            "length(summary_sha256) = 64",
            name="ck_assistant_checkpoint_hash",
        ),
        CheckConstraint(
            "estimated_tokens >= 0 AND estimated_characters >= 0",
            name="ck_assistant_checkpoint_estimates",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    source_start_sequence: Mapped[int] = mapped_column(Integer)
    through_sequence: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    summary_sha256: Mapped[str] = mapped_column(String(64))
    backend_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    estimated_tokens: Mapped[int] = mapped_column(Integer)
    estimated_characters: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


__all__ = [
    "AssistantAgentRunRecord",
    "AssistantCheckpointRecord",
    "AssistantContextSnapshotRecord",
    "AssistantFragmentRecord",
    "AssistantMessageRecord",
    "AssistantSessionRecord",
    "AssistantTaskRecord",
]
