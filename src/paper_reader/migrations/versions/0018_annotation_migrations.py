"""Add reviewable annotation migration previews and decisions.

Revision ID: 0018_annotation_migrations
Revises: 0017_reader_annotations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_annotation_migrations"
down_revision: str | None = "0017_reader_annotations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "annotation_migration_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("preview_key", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False),
        sa.Column("source_document_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("target_document_version_id", sa.String(length=36), nullable=False),
        sa.Column("target_document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_annotations_sha256", sa.String(length=64), nullable=False),
        sa.Column("target_extraction_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_document_version_id != target_document_version_id",
            name="ck_annotation_migration_distinct_documents",
        ),
        sa.CheckConstraint(
            "length(source_document_content_sha256) = 64",
            name="ck_annotation_migration_source_sha256_length",
        ),
        sa.CheckConstraint(
            "length(target_document_content_sha256) = 64",
            name="ck_annotation_migration_target_sha256_length",
        ),
        sa.CheckConstraint(
            "length(source_annotations_sha256) = 64",
            name="ck_annotation_migration_source_annotations_sha256_length",
        ),
        sa.CheckConstraint(
            "length(target_extraction_sha256) = 64",
            name="ck_annotation_migration_target_extraction_sha256_length",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preview_key", name="uq_annotation_migration_preview_key"),
    )
    op.create_index(
        "ix_annotation_migration_batches_preview_key",
        "annotation_migration_batches",
        ["preview_key"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_migration_batches_source_document_version_id",
        "annotation_migration_batches",
        ["source_document_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_migration_batches_target_document_version_id",
        "annotation_migration_batches",
        ["target_document_version_id"],
        unique=False,
    )

    op.create_table(
        "annotation_migration_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("source_annotation_id", sa.String(length=36), nullable=False),
        sa.Column("source_annotation_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("match_state", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("target_page_number", sa.Integer(), nullable=True),
        sa.Column("target_page_width", sa.Float(), nullable=True),
        sa.Column("target_page_height", sa.Float(), nullable=True),
        sa.Column("target_quote_text", sa.Text(), nullable=True),
        sa.Column("target_context_before", sa.Text(), nullable=True),
        sa.Column("target_context_after", sa.Text(), nullable=True),
        sa.Column("target_rects", sa.JSON(), nullable=False),
        sa.Column("text_source", sa.String(length=16), nullable=True),
        sa.Column("alternatives", sa.JSON(), nullable=False),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("order_index >= 0", name="ck_annotation_migration_order"),
        sa.CheckConstraint(
            "match_state IN ('matched', 'ambiguous', 'orphaned')",
            name="ck_annotation_migration_match_state",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low', 'none')",
            name="ck_annotation_migration_confidence",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_annotation_migration_score",
        ),
        sa.CheckConstraint(
            "text_source IS NULL OR text_source IN ('native', 'ocr')",
            name="ck_annotation_migration_text_source",
        ),
        sa.CheckConstraint(
            "length(source_annotation_sha256) = 64",
            name="ck_annotation_migration_annotation_sha256_length",
        ),
        sa.CheckConstraint(
            "length(candidate_sha256) = 64",
            name="ck_annotation_migration_candidate_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["annotation_migration_batches.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "source_annotation_id",
            name="uq_annotation_migration_candidate_source",
        ),
    )
    for column in ("batch_id", "source_annotation_id", "match_state"):
        op.create_index(
            f"ix_annotation_migration_candidates_{column}",
            "annotation_migration_candidates",
            [column],
            unique=False,
        )

    op.create_table(
        "annotation_migration_decisions",
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("target_annotation_id", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('accept', 'reject')",
            name="ck_annotation_migration_decision",
        ),
        sa.CheckConstraint(
            "(decision = 'accept' AND target_annotation_id IS NOT NULL) OR "
            "(decision = 'reject' AND target_annotation_id IS NULL)",
            name="ck_annotation_migration_decision_target",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["annotation_migration_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("candidate_id"),
    )

    for table in (
        "annotation_migration_batches",
        "annotation_migration_candidates",
        "annotation_migration_decisions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} rows are immutable');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} rows are immutable');
            END
            """
        )


def downgrade() -> None:
    for table in (
        "annotation_migration_decisions",
        "annotation_migration_candidates",
        "annotation_migration_batches",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
    op.drop_table("annotation_migration_decisions")
    for column in ("match_state", "source_annotation_id", "batch_id"):
        op.drop_index(
            f"ix_annotation_migration_candidates_{column}",
            table_name="annotation_migration_candidates",
        )
    op.drop_table("annotation_migration_candidates")
    for column in (
        "target_document_version_id",
        "source_document_version_id",
        "preview_key",
    ):
        op.drop_index(
            f"ix_annotation_migration_batches_{column}",
            table_name="annotation_migration_batches",
        )
    op.drop_table("annotation_migration_batches")
