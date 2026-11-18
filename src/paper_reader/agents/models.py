"""Persistence records owned by Agent configuration and prompt templates."""

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


class AgentSettingsRecord(Base):
    __tablename__ = "agent_settings"
    __table_args__ = (
        CheckConstraint("id = 'default'", name="ck_agent_settings_singleton"),
        CheckConstraint(
            "default_backend_id IN ('codex_cli', 'ollama_local', 'ollama_cloud')",
            name="ck_agent_settings_backend",
        ),
        CheckConstraint(
            "keyword_reasoning IN ('minimal', 'low', 'medium', 'high', 'xhigh') "
            "AND report_reasoning IN ('minimal', 'low', 'medium', 'high', 'xhigh') "
            "AND chat_reasoning IN ('minimal', 'low', 'medium', 'high', 'xhigh') "
            "AND comparison_reasoning IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_agent_settings_reasoning",
        ),
        CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_agent_settings_service_tier",
        ),
        CheckConstraint(
            "report_language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_agent_settings_language",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="default")
    default_backend_id: Mapped[str] = mapped_column(String(32), default="codex_cli")
    keyword_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    report_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    chat_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comparison_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    keyword_reasoning: Mapped[str] = mapped_column(String(16), default="medium")
    report_reasoning: Mapped[str] = mapped_column(String(16), default="high")
    chat_reasoning: Mapped[str] = mapped_column(String(16), default="medium")
    comparison_reasoning: Mapped[str] = mapped_column(String(16), default="high")
    service_tier: Mapped[str] = mapped_column(String(16), default="flex")
    report_language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    external_research_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PromptTemplateVersionRecord(Base):
    __tablename__ = "prompt_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "family",
            "name",
            "version",
            name="uq_prompt_template_family_name_version",
        ),
        CheckConstraint(
            "family IN ('general', 'ml_ai', 'biomedical', 'custom')",
            name="ck_prompt_template_family",
        ),
        CheckConstraint(
            "language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_prompt_template_language",
        ),
        CheckConstraint("version >= 1", name="ck_prompt_template_version"),
        CheckConstraint("length(body) >= 20", name="ck_prompt_template_body"),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_prompt_template_sha256_length",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    family: Mapped[str] = mapped_column(String(24), index=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    output_schema_version: Mapped[str] = mapped_column(String(80))
    built_in: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompt_template_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisReportRecord(Base):
    """One immutable, validated formal report version."""

    __tablename__ = "analysis_reports"
    __table_args__ = (
        UniqueConstraint("work_id", "version", name="uq_analysis_report_work_version"),
        UniqueConstraint("job_id", name="uq_analysis_report_job"),
        CheckConstraint("version >= 1", name="ck_analysis_report_version"),
        CheckConstraint(
            "evidence_level IN ('full_text', 'abstract_only')",
            name="ck_analysis_report_evidence_level",
        ),
        CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_analysis_report_reasoning",
        ),
        CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_analysis_report_service_tier",
        ),
        CheckConstraint(
            "language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_analysis_report_language",
        ),
        CheckConstraint(
            "network_policy IN ('local_evidence_only', 'external_research')",
            name="ck_analysis_report_network_policy",
        ),
        CheckConstraint(
            "(document_version_id IS NULL AND document_content_sha256 IS NULL) OR "
            "(document_version_id IS NOT NULL AND document_content_sha256 IS NOT NULL)",
            name="ck_analysis_report_document_pair",
        ),
        CheckConstraint(
            "length(evidence_sha256) = 64 AND length(prompt_sha256) = 64 "
            "AND length(request_fingerprint) = 64 AND length(report_sha256) = 64",
            name="ck_analysis_report_hashes",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        index=True,
    )
    document_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    document_content_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    evidence_level: Mapped[str] = mapped_column(String(24))
    evidence_sha256: Mapped[str] = mapped_column(String(64), index=True)
    evidence_manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    backend_id: Mapped[str] = mapped_column(String(80))
    requested_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reasoning_effort: Mapped[str] = mapped_column(String(16))
    service_tier: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(16))
    template_version_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_template_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    complete_prompt: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    network_policy: Mapped[str] = mapped_column(String(32))
    structured_report: Mapped[dict[str, Any]] = mapped_column(JSON)
    rendered_markdown: Mapped[str] = mapped_column(Text)
    external_citations: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(80))
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    report_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisReportFragmentRecord(Base):
    """One persist-before-stream fragment for a report Job."""

    __tablename__ = "analysis_report_fragments"
    __table_args__ = (
        CheckConstraint("sequence >= 0", name="ck_analysis_fragment_sequence"),
        CheckConstraint(
            "kind IN ('text', 'reasoning', 'diagnostic')",
            name="ck_analysis_fragment_kind",
        ),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisFailureDraftRecord(Base):
    """Bounded diagnostic retained when no formal report may be published."""

    __tablename__ = "analysis_failure_drafts"
    __table_args__ = (
        CheckConstraint(
            "repair_count >= 0 AND repair_count <= 1",
            name="ck_analysis_failure_repair_count",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_analysis_failure_fingerprint",
        ),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"),
        index=True,
    )
    error_category: Mapped[str] = mapped_column(String(80))
    error_message: Mapped[str] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text)
    validation_errors: Mapped[list[str]] = mapped_column(JSON)
    repair_count: Mapped[int] = mapped_column(Integer)
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
