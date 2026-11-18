"""Persist immutable, reproducible score snapshots.

Revision ID: 0010_score_snapshots
Revises: 0009_metadata_observations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_score_snapshots"
down_revision: str | None = "0009_metadata_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("context_key", sa.String(length=255), nullable=False),
        sa.Column("score_type", sa.String(length=24), nullable=False),
        sa.Column("rule_version", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("missing_policy", sa.JSON(), nullable=False),
        sa.Column("evidence_observation_ids", sa.JSON(), nullable=False),
        sa.Column("hard_constraints", sa.JSON(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "score_type IN ('relevance', 'quality', 'freshness', 'preference', 'composite')",
            name="ck_score_snapshot_type",
        ),
        sa.CheckConstraint("length(context_key) > 0", name="ck_score_snapshot_context"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_score_snapshot_score"),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 100",
            name="ck_score_snapshot_coverage",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_score_snapshot_confidence",
        ),
        sa.CheckConstraint(
            "length(snapshot_sha256) = 64",
            name="ck_score_snapshot_sha256_length",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["plan_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_id",
            "context_key",
            "score_type",
            "snapshot_sha256",
            name="uq_score_snapshot_identity",
        ),
    )
    for column in ("work_id", "run_id", "context_key", "score_type", "snapshot_sha256"):
        op.create_index(f"ix_score_snapshots_{column}", "score_snapshots", [column])
    op.execute(
        "CREATE TRIGGER trg_score_snapshots_no_update "
        "BEFORE UPDATE ON score_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'ScoreSnapshot is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_score_snapshots_no_delete "
        "BEFORE DELETE ON score_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'ScoreSnapshot is immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_score_snapshots_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_score_snapshots_no_update")
    for column in reversed(
        ("work_id", "run_id", "context_key", "score_type", "snapshot_sha256")
    ):
        op.drop_index(f"ix_score_snapshots_{column}", table_name="score_snapshots")
    op.drop_table("score_snapshots")
