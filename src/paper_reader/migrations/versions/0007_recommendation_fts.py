"""Add the derived multilingual FTS5 Work index.

Revision ID: 0007_recommendation_fts
Revises: 0006_merge_review_decisions
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_recommendation_fts"
down_revision: str | None = "0006_merge_review_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE VIRTUAL TABLE work_search USING fts5("
        "work_id UNINDEXED, title, abstract, authors, "
        "tokenize='unicode61 remove_diacritics 2')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS work_search")
