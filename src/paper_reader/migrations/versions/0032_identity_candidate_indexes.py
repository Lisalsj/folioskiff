"""Add bounded identity-candidate fallback indexes.

Revision ID: 0032_identity_candidate_indexes
Revises: 0031_library_query_indexes
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032_identity_candidate_indexes"
down_revision: str | None = "0031_library_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_works_status_normalized_title "
        "ON works (status ASC, normalized_title ASC, id ASC)"
    )
    op.execute(
        "CREATE INDEX ix_works_status_published_at "
        "ON works (status ASC, published_at ASC, id ASC)"
    )


def downgrade() -> None:
    op.drop_index("ix_works_status_published_at", table_name="works")
    op.drop_index("ix_works_status_normalized_title", table_name="works")
