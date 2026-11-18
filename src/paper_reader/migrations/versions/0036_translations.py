"""Add versioned paper translation artifacts.

Revision ID: 0036_translations
Revises: 0035_assistant_core
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_translations"
down_revision: str | None = "0035_assistant_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "translation_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("source_language", sa.String(length=16), nullable=False),
        sa.Column("target_language", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("evidence_level", sa.String(length=24), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_translation_artifact_version"),
        sa.CheckConstraint(
            "source_language IN ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'de', 'fr') "
            "AND target_language IN ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'de', 'fr')",
            name="ck_translation_artifact_languages",
        ),
        sa.CheckConstraint(
            "source_language != target_language",
            name="ck_translation_artifact_distinct_languages",
        ),
        sa.CheckConstraint(
            "scope IN ('selection', 'page', 'section', 'full_document')",
            name="ck_translation_artifact_scope",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_translation_artifact_status",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('native_text', 'ocr_text', 'mixed_text')",
            name="ck_translation_artifact_evidence_level",
        ),
        sa.CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_translation_artifact_document_hash",
        ),
        sa.CheckConstraint(
            "length(prompt_version) >= 1",
            name="ck_translation_artifact_prompt_version",
        ),
        sa.CheckConstraint(
            "(status IN ('draft', 'running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL)",
            name="ck_translation_artifact_completion",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["context_snapshot_id"],
            ["assistant_context_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["assistant_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_id",
            "version",
            name="uq_translation_artifact_work_version",
        ),
        sa.UniqueConstraint("task_id", name="uq_translation_artifact_task"),
        sa.UniqueConstraint("job_id", name="uq_translation_artifact_job"),
    )
    for column in (
        "work_id",
        "document_version_id",
        "document_content_sha256",
        "context_snapshot_id",
        "scope",
        "status",
    ):
        op.create_index(
            f"ix_translation_artifacts_{column}",
            "translation_artifacts",
            [column],
        )

    op.create_table(
        "translation_segments",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("block_numbers", sa.JSON(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("protected_map", sa.JSON(), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("segment_index >= 1", name="ck_translation_segment_index"),
        sa.CheckConstraint("page_number >= 1", name="ck_translation_segment_page"),
        sa.CheckConstraint(
            "length(source_sha256) = 64",
            name="ck_translation_segment_source_hash",
        ),
        sa.CheckConstraint(
            "source_kind IN ('native', 'ocr')",
            name="ck_translation_segment_source_kind",
        ),
        sa.CheckConstraint(
            "(source_kind = 'native' AND ocr_confidence IS NULL) OR "
            "(source_kind = 'ocr' AND ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_translation_segment_ocr_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'stale', 'failed')",
            name="ck_translation_segment_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'stale') OR translated_text IS NOT NULL",
            name="ck_translation_segment_translated_status",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["translation_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id",
            "segment_index",
            name="uq_translation_segment_artifact_index",
        ),
    )
    for column in ("artifact_id", "page_number", "source_sha256", "status"):
        op.create_index(
            f"ix_translation_segments_{column}",
            "translation_segments",
            [column],
        )

    op.create_table(
        "translation_terms",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("normalized_source_term", sa.String(length=500), nullable=False),
        sa.Column("source_term", sa.String(length=500), nullable=False),
        sa.Column("target_term", sa.String(length=1000), nullable=True),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(normalized_source_term) >= 1 AND length(source_term) >= 1",
            name="ck_translation_term_source",
        ),
        sa.CheckConstraint(
            "origin IN ('local_extraction', 'user', 'agent')",
            name="ck_translation_term_origin",
        ),
        sa.CheckConstraint(
            "locked = 0 OR target_term IS NOT NULL",
            name="ck_translation_term_locked_target",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["translation_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id",
            "normalized_source_term",
            name="uq_translation_term_artifact_source",
        ),
    )
    op.create_index(
        "ix_translation_terms_artifact_id",
        "translation_terms",
        ["artifact_id"],
    )

    op.create_table(
        "translation_exports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "format IN ('markdown', 'html', 'print_pdf')",
            name="ck_translation_export_format",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_translation_export_content_hash",
        ),
        sa.CheckConstraint(
            "length(relative_path) >= 1",
            name="ck_translation_export_relative_path",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["translation_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id",
            "format",
            "content_sha256",
            name="uq_translation_export_artifact_format_hash",
        ),
    )
    op.create_index(
        "ix_translation_exports_artifact_id",
        "translation_exports",
        ["artifact_id"],
    )
    op.create_index(
        "ix_translation_exports_content_sha256",
        "translation_exports",
        ["content_sha256"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_translation_exports_content_sha256",
        table_name="translation_exports",
    )
    op.drop_index(
        "ix_translation_exports_artifact_id",
        table_name="translation_exports",
    )
    op.drop_table("translation_exports")
    op.drop_index("ix_translation_terms_artifact_id", table_name="translation_terms")
    op.drop_table("translation_terms")
    for column in reversed(("artifact_id", "page_number", "source_sha256", "status")):
        op.drop_index(
            f"ix_translation_segments_{column}",
            table_name="translation_segments",
        )
    op.drop_table("translation_segments")
    for column in reversed(
        (
            "work_id",
            "document_version_id",
            "document_content_sha256",
            "context_snapshot_id",
            "scope",
            "status",
        )
    ):
        op.drop_index(
            f"ix_translation_artifacts_{column}",
            table_name="translation_artifacts",
        )
    op.drop_table("translation_artifacts")
