"""Preserve the Work and source evidence accepted by each discovery run.

Revision ID: 0008_run_candidates
Revises: 0007_recommendation_fts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_run_candidates"
down_revision: str | None = "0007_recommendation_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("match_kind", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "match_kind IN ('new', 'existing_source', 'exact_doi', "
            "'auto_similarity', 'review_required')",
            name="ck_run_candidate_match_kind",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["run_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["plan_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_record_id"], ["source_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_record_id", name="uq_run_candidate_source"),
    )
    op.create_index("ix_run_candidates_run_id", "run_candidates", ["run_id"])
    op.create_index("ix_run_candidates_batch_id", "run_candidates", ["batch_id"])
    op.create_index("ix_run_candidates_work_id", "run_candidates", ["work_id"])
    op.create_index(
        "ix_run_candidates_source_record_id",
        "run_candidates",
        ["source_record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_candidates_source_record_id", table_name="run_candidates")
    op.drop_index("ix_run_candidates_work_id", table_name="run_candidates")
    op.drop_index("ix_run_candidates_batch_id", table_name="run_candidates")
    op.drop_index("ix_run_candidates_run_id", table_name="run_candidates")
    op.drop_table("run_candidates")
