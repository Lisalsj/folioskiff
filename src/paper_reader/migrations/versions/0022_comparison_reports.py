"""Persist immutable, evidence-bound multi-paper comparisons.

Revision ID: 0022_comparison_reports
Revises: 0021_paper_conversations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_comparison_reports"
down_revision: str | None = "0021_paper_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "comparison_reports",
    "comparison_paper_evidence",
    "comparison_report_items",
    "comparison_fragments",
)


def upgrade() -> None:
    op.create_table(
        "comparison_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("comparison_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("backend_id", sa.String(length=80), nullable=False),
        sa.Column("requested_model_id", sa.String(length=200), nullable=True),
        sa.Column("resolved_model_id", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=False),
        sa.Column("service_tier", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("complete_prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("structured_report", sa.JSON(), nullable=False),
        sa.Column("rendered_markdown", sa.Text(), nullable=False),
        sa.Column("coverage", sa.Integer(), nullable=False),
        sa.Column("coverage_details", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_comparison_report_version"),
        sa.CheckConstraint(
            "mode IN ('direct', 'staged')",
            name="ck_comparison_report_mode",
        ),
        sa.CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_comparison_report_reasoning",
        ),
        sa.CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_comparison_report_service_tier",
        ),
        sa.CheckConstraint(
            "language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_comparison_report_language",
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 100",
            name="ck_comparison_report_coverage",
        ),
        sa.CheckConstraint(
            "length(comparison_key) = 64 AND length(prompt_sha256) = 64 "
            "AND length(request_fingerprint) = 64 AND length(report_sha256) = 64",
            name="ck_comparison_report_hashes",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "comparison_key",
            "version",
            name="uq_comparison_report_key_version",
        ),
        sa.UniqueConstraint("job_id", name="uq_comparison_report_job"),
    )
    for column in ("comparison_key", "job_id", "request_fingerprint"):
        op.create_index(
            f"ix_comparison_reports_{column}",
            "comparison_reports",
            [column],
        )

    op.create_table(
        "comparison_paper_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_job_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
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
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("structured_evidence", sa.JSON(), nullable=True),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("extraction_sha256", sa.String(length=64), nullable=True),
        sa.Column("reused_from_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_comparison_evidence_position"),
        sa.CheckConstraint(
            "evidence_level IN ('full_text', 'abstract_only')",
            name="ck_comparison_evidence_level",
        ),
        sa.CheckConstraint(
            "reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_comparison_evidence_reasoning",
        ),
        sa.CheckConstraint(
            "service_tier IN ('flex', 'fast')",
            name="ck_comparison_evidence_service_tier",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'failed')",
            name="ck_comparison_evidence_status",
        ),
        sa.CheckConstraint(
            "(status = 'complete' AND structured_evidence IS NOT NULL "
            "AND extraction_sha256 IS NOT NULL AND error_category IS NULL "
            "AND error_message IS NULL) OR "
            "(status = 'failed' AND structured_evidence IS NULL "
            "AND extraction_sha256 IS NULL AND error_category IS NOT NULL "
            "AND error_message IS NOT NULL)",
            name="ck_comparison_evidence_outcome",
        ),
        sa.CheckConstraint(
            "(document_version_id IS NULL AND document_content_sha256 IS NULL) OR "
            "(document_version_id IS NOT NULL AND document_content_sha256 IS NOT NULL)",
            name="ck_comparison_evidence_document_pair",
        ),
        sa.CheckConstraint(
            "length(evidence_sha256) = 64 AND length(prompt_sha256) = 64 "
            "AND (extraction_sha256 IS NULL OR length(extraction_sha256) = 64)",
            name="ck_comparison_evidence_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id"], ["jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reused_from_id"], ["comparison_paper_evidence.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_job_id",
            "position",
            name="uq_comparison_evidence_job_position",
        ),
    )
    for column in (
        "source_job_id",
        "work_id",
        "document_version_id",
        "evidence_sha256",
        "reused_from_id",
    ):
        op.create_index(
            f"ix_comparison_paper_evidence_{column}",
            "comparison_paper_evidence",
            [column],
        )

    op.create_table(
        "comparison_report_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=True),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_level", sa.String(length=24), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("extraction_status", sa.String(length=16), nullable=False),
        sa.Column("extraction_id", sa.String(length=36), nullable=True),
        sa.Column("coverage", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_comparison_item_position"),
        sa.CheckConstraint(
            "evidence_level IS NULL OR evidence_level IN ('full_text', 'abstract_only')",
            name="ck_comparison_item_evidence_level",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('direct', 'extracted', 'reused', 'failed', 'unavailable')",
            name="ck_comparison_item_extraction_status",
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 100",
            name="ck_comparison_item_coverage",
        ),
        sa.CheckConstraint(
            "(document_version_id IS NULL AND document_content_sha256 IS NULL) OR "
            "(document_version_id IS NOT NULL AND document_content_sha256 IS NOT NULL)",
            name="ck_comparison_item_document_pair",
        ),
        sa.CheckConstraint(
            "evidence_sha256 IS NULL OR length(evidence_sha256) = 64",
            name="ck_comparison_item_evidence_hash",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["comparison_reports.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["comparison_paper_evidence.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_id",
            "position",
            name="uq_comparison_report_item_position",
        ),
        sa.UniqueConstraint(
            "report_id",
            "work_id",
            name="uq_comparison_report_item_work",
        ),
    )
    for column in ("report_id", "work_id", "document_version_id", "extraction_id"):
        op.create_index(
            f"ix_comparison_report_items_{column}",
            "comparison_report_items",
            [column],
        )

    op.create_table(
        "comparison_fragments",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("item_position", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_comparison_fragment_sequence"),
        sa.CheckConstraint(
            "stage IN ('direct', 'extraction', 'batch', 'final')",
            name="ck_comparison_fragment_stage",
        ),
        sa.CheckConstraint(
            "item_position IS NULL OR item_position >= 0",
            name="ck_comparison_fragment_item_position",
        ),
        sa.CheckConstraint(
            "kind IN ('text', 'reasoning', 'diagnostic')",
            name="ck_comparison_fragment_kind",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id", "sequence"),
    )
    op.create_index(
        "ix_comparison_fragments_job_id",
        "comparison_fragments",
        ["job_id"],
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
    op.drop_index("ix_comparison_fragments_job_id", table_name="comparison_fragments")
    op.drop_table("comparison_fragments")
    for column in reversed(("report_id", "work_id", "document_version_id", "extraction_id")):
        op.drop_index(
            f"ix_comparison_report_items_{column}",
            table_name="comparison_report_items",
        )
    op.drop_table("comparison_report_items")
    for column in reversed(
        (
            "source_job_id",
            "work_id",
            "document_version_id",
            "evidence_sha256",
            "reused_from_id",
        )
    ):
        op.drop_index(
            f"ix_comparison_paper_evidence_{column}",
            table_name="comparison_paper_evidence",
        )
    op.drop_table("comparison_paper_evidence")
    for column in reversed(("comparison_key", "job_id", "request_fingerprint")):
        op.drop_index(
            f"ix_comparison_reports_{column}",
            table_name="comparison_reports",
        )
    op.drop_table("comparison_reports")
