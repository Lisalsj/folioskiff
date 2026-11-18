"""Add immutable score rule versions and an active selection.

Revision ID: 0013_score_rules
Revises: 0012_preferences
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_score_rules"
down_revision: str | None = "0012_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_rule_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_kind", sa.String(length=24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("rule_sha256", sa.String(length=64), nullable=False),
        sa.Column("parent_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rule_kind IN ('quality')", name="ck_score_rule_kind"),
        sa.CheckConstraint("revision >= 1", name="ck_score_rule_revision"),
        sa.CheckConstraint("length(name) > 0", name="ck_score_rule_name"),
        sa.CheckConstraint("length(rule_sha256) = 64", name="ck_score_rule_sha256"),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["score_rule_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_kind",
            "revision",
            name="uq_score_rule_kind_revision",
        ),
        sa.UniqueConstraint(
            "rule_kind",
            "rule_sha256",
            name="uq_score_rule_kind_sha256",
        ),
    )
    op.create_index(
        "ix_score_rule_versions_rule_kind",
        "score_rule_versions",
        ["rule_kind"],
    )
    op.create_index(
        "ix_score_rule_versions_rule_sha256",
        "score_rule_versions",
        ["rule_sha256"],
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_score_rule_versions_no_{operation.casefold()} "
            f"BEFORE {operation} ON score_rule_versions "
            "BEGIN SELECT RAISE(ABORT, 'ScoreRuleVersion is immutable'); END"
        )

    op.create_table(
        "score_rule_selections",
        sa.Column("rule_kind", sa.String(length=24), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rule_kind IN ('quality')",
            name="ck_score_rule_selection_kind",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["score_rule_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("rule_kind"),
        sa.UniqueConstraint("version_id"),
    )
    op.create_index(
        "ix_score_rule_selections_version_id",
        "score_rule_selections",
        ["version_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_score_rule_selections_version_id",
        table_name="score_rule_selections",
    )
    op.drop_table("score_rule_selections")
    for operation in ("delete", "update"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_score_rule_versions_no_{operation}")
    op.drop_index(
        "ix_score_rule_versions_rule_sha256",
        table_name="score_rule_versions",
    )
    op.drop_index(
        "ix_score_rule_versions_rule_kind",
        table_name="score_rule_versions",
    )
    op.drop_table("score_rule_versions")
