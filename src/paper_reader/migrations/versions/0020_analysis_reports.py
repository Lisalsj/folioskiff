"""Persist immutable structured analysis reports and failure drafts.

Revision ID: 0020_analysis_reports
Revises: 0019_agent_templates
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_analysis_reports"
down_revision: str | None = "0019_agent_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "analysis_reports",
    "analysis_report_fragments",
    "analysis_failure_drafts",
)


def upgrade() -> None:
    op.create_table(
        "analysis_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=True),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_level", sa.String(length=24), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=False),
        sa.Column("requested_model_id", sa.String(length=200), nullable=True),
        sa.Column("resolved_model_id", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=False),
        sa.Column("service_tier", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("template_version_id", sa.String(length=80), nullable=False),
        sa.Column("complete_prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("network_policy", sa.String(length=32), nullable=False),
        sa.Column("structured_report", sa.JSON(), nullable=False),
        sa.Column("rendered_markdown", sa.Text(), nullable=False),
        sa.Column("external_citations", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_analysis_report_version"),
        sa.CheckConstraint(
            "evidence_level IN ('full_text', 'abstract_only')",
            name="ck_analysis_report_evidence_level",
        ),
        sa.CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_analysis_report_reasoning",
        ),
        sa.CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_analysis_report_service_tier",
        ),
        sa.CheckConstraint(
            "language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_analysis_report_language",
        ),
        sa.CheckConstraint(
            "network_policy IN ('local_evidence_only', 'external_research')",
            name="ck_analysis_report_network_policy",
        ),
        sa.CheckConstraint(
            "(document_version_id IS NULL AND document_content_sha256 IS NULL) OR "
            "(document_version_id IS NOT NULL AND document_content_sha256 IS NOT NULL)",
            name="ck_analysis_report_document_pair",
        ),
        sa.CheckConstraint(
            "length(evidence_sha256) = 64 AND length(prompt_sha256) = 64 "
            "AND length(request_fingerprint) = 64 AND length(report_sha256) = 64",
            name="ck_analysis_report_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["work_id"],
            ["works.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["prompt_template_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_id",
            "version",
            name="uq_analysis_report_work_version",
        ),
        sa.UniqueConstraint("job_id", name="uq_analysis_report_job"),
    )
    for column in (
        "work_id",
        "job_id",
        "document_version_id",
        "evidence_sha256",
        "template_version_id",
        "request_fingerprint",
    ):
        op.create_index(f"ix_analysis_reports_{column}", "analysis_reports", [column])

    op.create_table(
        "analysis_report_fragments",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_analysis_fragment_sequence"),
        sa.CheckConstraint(
            "kind IN ('text', 'reasoning', 'diagnostic')",
            name="ck_analysis_fragment_kind",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id", "sequence"),
    )
    op.create_index(
        "ix_analysis_report_fragments_job_id",
        "analysis_report_fragments",
        ["job_id"],
    )

    op.create_table(
        "analysis_failure_drafts",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("error_category", sa.String(length=80), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("repair_count", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "repair_count >= 0 AND repair_count <= 1",
            name="ck_analysis_failure_repair_count",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_analysis_failure_fingerprint",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_analysis_failure_drafts_work_id",
        "analysis_failure_drafts",
        ["work_id"],
    )
    op.create_index(
        "ix_analysis_failure_drafts_request_fingerprint",
        "analysis_failure_drafts",
        ["request_fingerprint"],
    )

    op.execute(
        "CREATE TRIGGER analysis_reports_no_failure "
        "BEFORE INSERT ON analysis_reports "
        "WHEN EXISTS (SELECT 1 FROM analysis_failure_drafts WHERE job_id = NEW.job_id) "
        "BEGIN SELECT RAISE(ABORT, 'formal report and failure draft are exclusive'); END"
    )
    op.execute(
        "CREATE TRIGGER analysis_failures_no_report "
        "BEFORE INSERT ON analysis_failure_drafts "
        "WHEN EXISTS (SELECT 1 FROM analysis_reports WHERE job_id = NEW.job_id) "
        "BEGIN SELECT RAISE(ABORT, 'formal report and failure draft are exclusive'); END"
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
    op.execute("DROP TRIGGER IF EXISTS analysis_failures_no_report")
    op.execute("DROP TRIGGER IF EXISTS analysis_reports_no_failure")
    op.drop_index(
        "ix_analysis_failure_drafts_request_fingerprint",
        table_name="analysis_failure_drafts",
    )
    op.drop_index(
        "ix_analysis_failure_drafts_work_id",
        table_name="analysis_failure_drafts",
    )
    op.drop_table("analysis_failure_drafts")
    op.drop_index(
        "ix_analysis_report_fragments_job_id",
        table_name="analysis_report_fragments",
    )
    op.drop_table("analysis_report_fragments")
    for column in reversed(
        (
            "work_id",
            "job_id",
            "document_version_id",
            "evidence_sha256",
            "template_version_id",
            "request_fingerprint",
        )
    ):
        op.drop_index(f"ix_analysis_reports_{column}", table_name="analysis_reports")
    op.drop_table("analysis_reports")
