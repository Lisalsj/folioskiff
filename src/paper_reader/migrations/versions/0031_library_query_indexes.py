"""Add ordered library and representative-document query indexes.

Revision ID: 0031_library_query_indexes
Revises: 0030_user_preferences
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031_library_query_indexes"
down_revision: str | None = "0030_user_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_works_status_doi ON works (status ASC, doi ASC)")
    op.execute(
        "CREATE INDEX ix_library_entries_created_work "
        "ON library_entries (created_at DESC, work_id ASC)"
    )
    op.execute(
        "CREATE INDEX ix_document_versions_work_representative "
        "ON document_versions "
        "(work_id ASC, source_updated_at DESC, completeness DESC, created_at DESC, id ASC)"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_versions_work_representative",
        table_name="document_versions",
    )
    op.drop_index("ix_library_entries_created_work", table_name="library_entries")
    op.drop_index("ix_works_status_doi", table_name="works")
