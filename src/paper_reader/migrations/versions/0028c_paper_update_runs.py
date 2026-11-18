"""Attribute paper updates to their originating discovery run.

Revision ID: 0028c_paper_update_runs
Revises: 0028b_discovery_quota_counts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028c_paper_update_runs"
down_revision: str | None = "0028b_discovery_quota_counts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("paper_updates", recreate="always") as batch:
        batch.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_paper_updates_run_id_plan_runs",
            "plan_runs",
            ["run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_paper_updates_run_id", ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("paper_updates", recreate="always") as batch:
        batch.drop_index("ix_paper_updates_run_id")
        batch.drop_constraint("fk_paper_updates_run_id_plan_runs", type_="foreignkey")
        batch.drop_column("run_id")
