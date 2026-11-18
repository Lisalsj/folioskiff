"""Persist immutable recommendation sets and their scored identities.

Revision ID: 0029_recommendation_sets
Revises: 0028c_paper_update_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_recommendation_sets"
down_revision: str | None = "0028c_paper_update_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recommendation_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("keyword_version_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ranking_rule_version", sa.String(length=80), nullable=False),
        sa.Column("fixed_historical_months", sa.Boolean(), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("candidate_cap", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("recommendation_count", sa.Integer(), nullable=False),
        sa.Column("exploration_target", sa.Integer(), nullable=False),
        sa.Column("exploration_count", sa.Integer(), nullable=False),
        sa.Column("degradation_reasons", sa.JSON(), nullable=False),
        sa.Column("set_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_recommendation_set_version"),
        sa.CheckConstraint(
            "requested_limit >= 1 AND requested_limit <= 50",
            name="ck_recommendation_set_requested_limit",
        ),
        sa.CheckConstraint(
            "candidate_cap >= requested_limit AND candidate_cap <= 50",
            name="ck_recommendation_set_candidate_cap",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0 AND recommendation_count >= 0 "
            "AND recommendation_count <= candidate_count",
            name="ck_recommendation_set_counts",
        ),
        sa.CheckConstraint(
            "exploration_target >= 0 AND exploration_count >= 0 "
            "AND exploration_count <= exploration_target "
            "AND exploration_target <= recommendation_count",
            name="ck_recommendation_set_exploration",
        ),
        sa.CheckConstraint(
            "length(ranking_rule_version) > 0",
            name="ck_recommendation_set_rule_version",
        ),
        sa.CheckConstraint(
            "length(set_sha256) = 64",
            name="ck_recommendation_set_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["keyword_version_id"],
            ["keyword_set_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["plan_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "set_sha256",
            name="uq_recommendation_set_run_sha256",
        ),
        sa.UniqueConstraint(
            "run_id",
            "version",
            name="uq_recommendation_set_run_version",
        ),
    )
    for column in ("run_id", "keyword_version_id", "set_sha256"):
        op.create_index(
            f"ix_recommendation_sets_{column}",
            "recommendation_sets",
            [column],
        )

    op.create_table(
        "recommendation_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recommendation_set_id", sa.String(length=36), nullable=False),
        sa.Column("scored_work_id", sa.String(length=36), nullable=False),
        sa.Column("default_position", sa.Integer(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("historical_match", sa.Boolean(), nullable=False),
        sa.Column("eligible_by_default", sa.Boolean(), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=True),
        sa.Column("ineligibility_reasons", sa.JSON(), nullable=False),
        sa.Column("exploration_reasons", sa.JSON(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("relevance_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("quality_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("freshness_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("preference_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("composite_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("ranking_evidence", sa.JSON(), nullable=False),
        sa.Column("item_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(selected = 1 AND historical_match = 0 "
            "AND default_position IS NOT NULL "
            "AND channel IN ('exploitation', 'exploration')) OR "
            "(selected = 0 AND default_position IS NULL AND channel IS NULL)",
            name="ck_recommendation_item_selection",
        ),
        sa.CheckConstraint(
            "default_position IS NULL OR default_position >= 1",
            name="ck_recommendation_item_position",
        ),
        sa.CheckConstraint(
            "length(item_sha256) = 64",
            name="ck_recommendation_item_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["composite_snapshot_id"],
            ["score_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["freshness_snapshot_id"],
            ["score_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preference_snapshot_id"],
            ["score_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quality_snapshot_id"],
            ["score_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_set_id"],
            ["recommendation_sets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relevance_snapshot_id"],
            ["score_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scored_work_id"],
            ["works.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recommendation_set_id",
            "default_position",
            name="uq_recommendation_item_set_position",
        ),
        sa.UniqueConstraint(
            "recommendation_set_id",
            "scored_work_id",
            name="uq_recommendation_item_set_work",
        ),
    )
    for column in (
        "recommendation_set_id",
        "scored_work_id",
        "historical_match",
        "eligible_by_default",
        "relevance_snapshot_id",
        "quality_snapshot_id",
        "freshness_snapshot_id",
        "preference_snapshot_id",
        "composite_snapshot_id",
        "item_sha256",
    ):
        op.create_index(
            f"ix_recommendation_items_{column}",
            "recommendation_items",
            [column],
        )

    for table, label in (
        ("recommendation_sets", "RecommendationSet"),
        ("recommendation_items", "RecommendationItem"),
    ):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                f"BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{label} is immutable'); END"
            )


def downgrade() -> None:
    for table in ("recommendation_items", "recommendation_sets"):
        for operation in ("delete", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation}")
    for column in reversed(
        (
            "recommendation_set_id",
            "scored_work_id",
            "historical_match",
            "eligible_by_default",
            "relevance_snapshot_id",
            "quality_snapshot_id",
            "freshness_snapshot_id",
            "preference_snapshot_id",
            "composite_snapshot_id",
            "item_sha256",
        )
    ):
        op.drop_index(f"ix_recommendation_items_{column}", table_name="recommendation_items")
    op.drop_table("recommendation_items")
    for column in reversed(("run_id", "keyword_version_id", "set_sha256")):
        op.drop_index(f"ix_recommendation_sets_{column}", table_name="recommendation_sets")
    op.drop_table("recommendation_sets")
