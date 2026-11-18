"""Retain audited manual merge decisions and canonical Work aliases.

Revision ID: 0006_merge_review_decisions
Revises: 0005_discovery_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_merge_review_decisions"
down_revision: str | None = "0005_discovery_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("works") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(sa.Column("merged_into_work_id", sa.String(length=36), nullable=True))
        batch.create_check_constraint(
            "ck_work_status",
            "status IN ('active', 'merged')",
        )
        batch.create_check_constraint(
            "ck_work_merge_target",
            "(status = 'active' AND merged_into_work_id IS NULL) OR "
            "(status = 'merged' AND merged_into_work_id IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_works_merged_into_work_id",
            "works",
            ["merged_into_work_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_works_status", "works", ["status"])
    op.create_index(
        "ix_works_merged_into_work_id",
        "works",
        ["merged_into_work_id"],
    )

    with op.batch_alter_table("merge_reviews") as batch:
        batch.add_column(sa.Column("decision_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("decided_by", sa.String(length=24), nullable=True))
        batch.create_check_constraint(
            "ck_merge_review_status",
            "status IN ('pending', 'merged', 'kept_separate', 'superseded')",
        )


def downgrade() -> None:
    with op.batch_alter_table("merge_reviews") as batch:
        batch.drop_constraint("ck_merge_review_status", type_="check")
        batch.drop_column("decided_by")
        batch.drop_column("decision_note")

    op.drop_index("ix_works_merged_into_work_id", table_name="works")
    op.drop_index("ix_works_status", table_name="works")
    with op.batch_alter_table("works") as batch:
        batch.drop_constraint("fk_works_merged_into_work_id", type_="foreignkey")
        batch.drop_constraint("ck_work_merge_target", type_="check")
        batch.drop_constraint("ck_work_status", type_="check")
        batch.drop_column("merged_into_work_id")
        batch.drop_column("status")
