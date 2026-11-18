"""Create immutable keyword versions and append-only confirmation facts.

Revision ID: 0003_keyword_versions
Revises: 0002_discovery_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_keyword_versions"
down_revision: str | None = "0002_discovery_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "keyword_set_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("parent_version_id", sa.String(length=36), nullable=True),
        sa.Column("research_intent", sa.Text(), nullable=False),
        sa.Column("required_groups", sa.JSON(), nullable=False),
        sa.Column("boost_groups", sa.JSON(), nullable=False),
        sa.Column("excluded_groups", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("generator_info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["keyword_set_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_keyword_set_versions_parent_version_id",
        "keyword_set_versions",
        ["parent_version_id"],
    )
    op.create_table(
        "keyword_confirmations",
        sa.Column("keyword_version_id", sa.String(length=36), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["keyword_version_id"],
            ["keyword_set_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("keyword_version_id"),
    )

    op.execute(
        """
        CREATE TRIGGER trg_keyword_versions_no_update
        BEFORE UPDATE ON keyword_set_versions
        BEGIN SELECT RAISE(ABORT, 'KeywordSetVersion is immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_keyword_versions_no_delete
        BEFORE DELETE ON keyword_set_versions
        BEGIN SELECT RAISE(ABORT, 'KeywordSetVersion is immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_keyword_confirmations_no_update
        BEFORE UPDATE ON keyword_confirmations
        BEGIN SELECT RAISE(ABORT, 'Keyword confirmation is immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_keyword_confirmations_no_delete
        BEFORE DELETE ON keyword_confirmations
        BEGIN SELECT RAISE(ABORT, 'Keyword confirmation is immutable'); END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_keyword_confirmations_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_keyword_confirmations_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_keyword_versions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_keyword_versions_no_update")
    op.drop_table("keyword_confirmations")
    op.drop_index(
        "ix_keyword_set_versions_parent_version_id",
        table_name="keyword_set_versions",
    )
    op.drop_table("keyword_set_versions")
