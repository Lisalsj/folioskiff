"""Create immutable plan-run snapshots and resumable source-month batches.

Revision ID: 0005_discovery_runs
Revises: 0004_discovery_plans
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_discovery_runs"
down_revision: str | None = "0004_discovery_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("keyword_version_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("months", sa.JSON(), nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("keyword_snapshot", sa.JSON(), nullable=False),
        sa.Column("connector_versions", sa.JSON(), nullable=False),
        sa.Column("total_batches", sa.Integer(), nullable=False),
        sa.Column("succeeded_batches", sa.Integer(), nullable=False),
        sa.Column("failed_batches", sa.Integer(), nullable=False),
        sa.Column("cancelled_batches", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial_success', "
            "'failed', 'cancelled')",
            name="ck_plan_run_status",
        ),
        sa.CheckConstraint(
            "total_batches >= 1 AND succeeded_batches >= 0 AND failed_batches >= 0 "
            "AND cancelled_batches >= 0 AND "
            "succeeded_batches + failed_batches + cancelled_batches <= total_batches",
            name="ck_plan_run_batch_counts",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["keyword_version_id"],
            ["keyword_set_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["discovery_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_plan_runs_plan_id", "plan_runs", ["plan_id"])
    op.create_index("ix_plan_runs_job_id", "plan_runs", ["job_id"], unique=True)
    op.create_index(
        "ix_plan_runs_keyword_version_id",
        "plan_runs",
        ["keyword_version_id"],
    )
    op.create_index("ix_plan_runs_status", "plan_runs", ["status"])

    op.create_table(
        "run_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("connector_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("query_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("ingested_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_run_batch_status",
        ),
        sa.CheckConstraint(
            "fetched_count >= 0 AND accepted_count >= 0 AND ingested_count >= 0 "
            "AND attempt_count >= 0",
            name="ck_run_batch_counts",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["plan_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "month", "connector_id", name="uq_run_batch_scope"),
    )
    op.create_index("ix_run_batches_run_id", "run_batches", ["run_id"])
    op.create_index("ix_run_batches_month", "run_batches", ["month"])
    op.create_index("ix_run_batches_connector_id", "run_batches", ["connector_id"])
    op.create_index("ix_run_batches_status", "run_batches", ["status"])


def downgrade() -> None:
    op.drop_index("ix_run_batches_status", table_name="run_batches")
    op.drop_index("ix_run_batches_connector_id", table_name="run_batches")
    op.drop_index("ix_run_batches_month", table_name="run_batches")
    op.drop_index("ix_run_batches_run_id", table_name="run_batches")
    op.drop_table("run_batches")
    op.drop_index("ix_plan_runs_status", table_name="plan_runs")
    op.drop_index("ix_plan_runs_keyword_version_id", table_name="plan_runs")
    op.drop_index("ix_plan_runs_job_id", table_name="plan_runs")
    op.drop_index("ix_plan_runs_plan_id", table_name="plan_runs")
    op.drop_table("plan_runs")
