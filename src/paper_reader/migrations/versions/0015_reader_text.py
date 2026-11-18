"""Add current page-level native text and future OCR evidence fields.

Revision ID: 0015_reader_text
Revises: 0014_reader_library
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_reader_text"
down_revision: str | None = "0014_reader_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_page_texts",
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_width", sa.Float(), nullable=False),
        sa.Column("page_height", sa.Float(), nullable=False),
        sa.Column("native_text", sa.Text(), nullable=False),
        sa.Column("native_blocks", sa.JSON(), nullable=False),
        sa.Column("native_character_count", sa.Integer(), nullable=False),
        sa.Column("likely_scanned", sa.Boolean(), nullable=False),
        sa.Column("ocr_state", sa.String(length=24), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("ocr_blocks", sa.JSON(), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("extractor_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page_number >= 1", name="ck_document_page_number"),
        sa.CheckConstraint(
            "page_width > 0 AND page_height > 0",
            name="ck_document_page_dimensions",
        ),
        sa.CheckConstraint(
            "native_character_count >= 0",
            name="ck_document_page_character_count",
        ),
        sa.CheckConstraint(
            "ocr_state IN ('not_needed', 'pending', 'complete', 'failed', 'unavailable')",
            name="ck_document_page_ocr_state",
        ),
        sa.CheckConstraint(
            "ocr_confidence IS NULL OR "
            "(ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ck_document_page_ocr_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_version_id", "page_number"),
    )
    op.create_index(
        "ix_document_page_texts_ocr_state",
        "document_page_texts",
        ["ocr_state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_page_texts_ocr_state",
        table_name="document_page_texts",
    )
    op.drop_table("document_page_texts")
