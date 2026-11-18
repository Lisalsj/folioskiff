"""Add local library membership and stored-document metadata.

Revision ID: 0014_reader_library
Revises: 0013_score_rules
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_reader_library"
down_revision: str | None = "0013_score_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_versions", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "byte_size",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(sa.Column("original_filename", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "acquisition_kind",
                sa.String(length=24),
                nullable=False,
                server_default="source",
            )
        )
        batch.add_column(sa.Column("acquisition_url", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_document_byte_size",
            "byte_size >= 0",
        )
        batch.create_check_constraint(
            "ck_document_acquisition_kind",
            "acquisition_kind IN ('source', 'manual_import', 'download')",
        )
        batch.create_index(
            "ix_document_versions_last_accessed_at",
            ["last_accessed_at"],
            unique=False,
        )

    op.create_table(
        "library_entries",
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("added_via", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "added_via IN ('pdf', 'doi', 'bibtex', 'url', 'save')",
            name="ck_library_entry_added_via",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_id"),
    )
    op.create_index(
        "ix_library_entries_added_via",
        "library_entries",
        ["added_via"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_library_entries_added_via", table_name="library_entries")
    op.drop_table("library_entries")

    with op.batch_alter_table("document_versions", recreate="always") as batch:
        batch.drop_index("ix_document_versions_last_accessed_at")
        batch.drop_constraint("ck_document_acquisition_kind", type_="check")
        batch.drop_constraint("ck_document_byte_size", type_="check")
        batch.drop_column("last_accessed_at")
        batch.drop_column("acquisition_url")
        batch.drop_column("acquisition_kind")
        batch.drop_column("original_filename")
        batch.drop_column("byte_size")
