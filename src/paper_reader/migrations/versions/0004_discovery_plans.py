"""Create month-scoped discovery plans and pending catch-up occurrences.

Revision ID: 0004_discovery_plans
Revises: 0003_keyword_versions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_discovery_plans"
down_revision: str | None = "0003_keyword_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("research_intent", sa.Text(), nullable=False),
        sa.Column("keyword_version_id", sa.String(length=36), nullable=False),
        sa.Column("date_mode", sa.String(length=16), nullable=False),
        sa.Column("start_month", sa.String(length=7), nullable=True),
        sa.Column("end_month", sa.String(length=7), nullable=True),
        sa.Column("rolling_months", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("source_filters", sa.JSON(), nullable=False),
        sa.Column("schedule_frequency", sa.String(length=16), nullable=False),
        sa.Column("schedule_local_time", sa.String(length=5), nullable=False),
        sa.Column("weekly_day", sa.Integer(), nullable=False),
        sa.Column("monthly_day", sa.Integer(), nullable=False),
        sa.Column("priority_limit", sa.Integer(), nullable=False),
        sa.Column("candidate_limit", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(date_mode = 'fixed' AND start_month IS NOT NULL AND end_month IS NOT NULL "
            "AND rolling_months IS NULL) OR "
            "(date_mode = 'rolling' AND start_month IS NULL AND end_month IS NULL "
            "AND rolling_months >= 1)",
            name="ck_discovery_plan_date_mode",
        ),
        sa.CheckConstraint(
            "schedule_frequency IN ('manual', 'daily', 'weekly', 'monthly')",
            name="ck_discovery_plan_schedule_frequency",
        ),
        sa.CheckConstraint(
            "weekly_day >= 0 AND weekly_day <= 6",
            name="ck_discovery_plan_weekly_day",
        ),
        sa.CheckConstraint(
            "monthly_day >= 1 AND monthly_day <= 28",
            name="ck_discovery_plan_monthly_day",
        ),
        sa.CheckConstraint(
            "priority_limit >= 1 AND candidate_limit >= priority_limit",
            name="ck_discovery_plan_quotas",
        ),
        sa.ForeignKeyConstraint(
            ["keyword_version_id"],
            ["keyword_set_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovery_plans_keyword_version_id",
        "discovery_plans",
        ["keyword_version_id"],
    )
    op.create_index(
        "ix_discovery_plans_schedule_frequency",
        "discovery_plans",
        ["schedule_frequency"],
    )
    op.create_index("ix_discovery_plans_enabled", "discovery_plans", ["enabled"])

    op.create_table(
        "plan_catchups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'dismissed')",
            name="ck_plan_catchup_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["discovery_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "scheduled_for", name="uq_plan_catchup_occurrence"),
    )
    op.create_index("ix_plan_catchups_plan_id", "plan_catchups", ["plan_id"])
    op.create_index("ix_plan_catchups_scheduled_for", "plan_catchups", ["scheduled_for"])
    op.create_index("ix_plan_catchups_status", "plan_catchups", ["status"])


def downgrade() -> None:
    op.drop_index("ix_plan_catchups_status", table_name="plan_catchups")
    op.drop_index("ix_plan_catchups_scheduled_for", table_name="plan_catchups")
    op.drop_index("ix_plan_catchups_plan_id", table_name="plan_catchups")
    op.drop_table("plan_catchups")
    op.drop_index("ix_discovery_plans_enabled", table_name="discovery_plans")
    op.drop_index("ix_discovery_plans_schedule_frequency", table_name="discovery_plans")
    op.drop_index("ix_discovery_plans_keyword_version_id", table_name="discovery_plans")
    op.drop_table("discovery_plans")
