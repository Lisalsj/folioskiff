"""Persist discovery classifications separately from the new-paper quota.

Revision ID: 0028b_discovery_quota_counts
Revises: 0028_source_revisions_and_updates
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028b_discovery_quota_counts"
down_revision: str | None = "0028_source_revisions_and_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run_batches", recreate="always") as batch:
        batch.add_column(
            sa.Column("new_work_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("update_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_check_constraint(
            "ck_run_batch_discovery_counts",
            "new_work_count >= 0 AND update_count >= 0 AND duplicate_count >= 0 "
            "AND scanned_count >= 0",
        )

    op.execute("UPDATE run_batches SET scanned_count = fetched_count")
    op.execute(
        """
        UPDATE run_batches
        SET new_work_count = (
            SELECT count(DISTINCT candidate.work_id)
            FROM run_candidates AS candidate
            WHERE candidate.batch_id = run_batches.id
              AND candidate.discovery_kind = 'new_work'
        ),
        update_count = (
            SELECT count(*)
            FROM run_candidates AS candidate
            WHERE candidate.batch_id = run_batches.id
              AND candidate.discovery_kind IN ('metadata_refresh', 'important_revision')
        ),
        duplicate_count = (
            SELECT count(*)
            FROM run_candidates AS candidate
            WHERE candidate.batch_id = run_batches.id
              AND candidate.discovery_kind IN ('existing_work_new_source', 'unchanged')
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("run_batches", recreate="always") as batch:
        batch.drop_constraint("ck_run_batch_discovery_counts", type_="check")
        batch.drop_column("scanned_count")
        batch.drop_column("duplicate_count")
        batch.drop_column("update_count")
        batch.drop_column("new_work_count")
