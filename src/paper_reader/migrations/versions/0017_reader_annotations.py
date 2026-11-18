"""Add immutable PDF reading positions and user annotations.

Revision ID: 0017_reader_annotations
Revises: 0016_reader_ocr
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_reader_annotations"
down_revision: str | None = "0016_reader_ocr"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reading_positions",
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("scale", sa.Float(), nullable=False),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_reading_position_sha256_length",
        ),
        sa.CheckConstraint(
            "page_number >= 1 AND page_count >= 1 AND page_number <= page_count",
            name="ck_reading_position_page_bounds",
        ),
        sa.CheckConstraint(
            "scale >= 0.5 AND scale <= 3",
            name="ck_reading_position_scale",
        ),
        sa.CheckConstraint(
            "rotation IN (0, 90, 180, 270)",
            name="ck_reading_position_rotation",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_reading_position_progress",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_version_id"),
    )
    op.create_index(
        "ix_reading_positions_document_content_sha256",
        "reading_positions",
        ["document_content_sha256"],
        unique=False,
    )

    op.create_table(
        "annotations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("document_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_width", sa.Float(), nullable=False),
        sa.Column("page_height", sa.Float(), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("context_before", sa.Text(), nullable=True),
        sa.Column("context_after", sa.Text(), nullable=True),
        sa.Column("rects", sa.JSON(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(document_content_sha256) = 64",
            name="ck_annotation_sha256_length",
        ),
        sa.CheckConstraint(
            "kind IN ('highlight', 'note', 'bookmark')",
            name="ck_annotation_kind",
        ),
        sa.CheckConstraint("page_number >= 1", name="ck_annotation_page_number"),
        sa.CheckConstraint(
            "page_width > 0 AND page_height > 0",
            name="ck_annotation_page_dimensions",
        ),
        sa.CheckConstraint(
            "color IN ('amber', 'green', 'rose', 'blue')",
            name="ck_annotation_color",
        ),
        sa.CheckConstraint(
            "quote_text IS NULL OR length(quote_text) <= 10000",
            name="ck_annotation_quote_length",
        ),
        sa.CheckConstraint(
            "context_before IS NULL OR length(context_before) <= 2000",
            name="ck_annotation_context_before_length",
        ),
        sa.CheckConstraint(
            "context_after IS NULL OR length(context_after) <= 2000",
            name="ck_annotation_context_after_length",
        ),
        sa.CheckConstraint(
            "note IS NULL OR length(note) <= 20000",
            name="ck_annotation_note_length",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "document_version_id",
        "document_content_sha256",
        "kind",
        "page_number",
    ):
        op.create_index(
            f"ix_annotations_{column}",
            "annotations",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in (
        "page_number",
        "kind",
        "document_content_sha256",
        "document_version_id",
    ):
        op.drop_index(f"ix_annotations_{column}", table_name="annotations")
    op.drop_table("annotations")
    op.drop_index(
        "ix_reading_positions_document_content_sha256",
        table_name="reading_positions",
    )
    op.drop_table("reading_positions")
