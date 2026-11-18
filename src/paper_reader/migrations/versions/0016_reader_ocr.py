"""Add auditable OCR engine and model provenance to page evidence.

Revision ID: 0016_reader_ocr
Revises: 0015_reader_text
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_reader_ocr"
down_revision: str | None = "0015_reader_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_page_texts",
        sa.Column("ocr_engine_version", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "document_page_texts",
        sa.Column("ocr_model_bundle_id", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_page_texts", "ocr_model_bundle_id")
    op.drop_column("document_page_texts", "ocr_engine_version")
