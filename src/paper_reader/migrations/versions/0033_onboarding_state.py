"""Persist the guided first-run state.

Revision ID: 0033_onboarding_state
Revises: 0032_identity_candidate_indexes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_onboarding_state"
down_revision: str | None = "0032_identity_candidate_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_STATUSES = (
    '{"agents":"pending","reading_preferences":"pending",'
    '"sources":"pending","storage_privacy":"pending"}'
)


def upgrade() -> None:
    op.create_table(
        "onboarding_state",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("current_step", sa.String(length=32), nullable=False),
        sa.Column("step_statuses", sa.Text(), nullable=False),
        sa.Column("privacy_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 'default'", name="ck_onboarding_state_singleton"),
        sa.CheckConstraint(
            "current_step IN ('storage_privacy', 'agents', 'sources', "
            "'reading_preferences', 'complete')",
            name="ck_onboarding_state_current_step",
        ),
        sa.CheckConstraint(
            "json_valid(step_statuses)",
            name="ck_onboarding_state_statuses_json",
        ),
        sa.CheckConstraint(
            "(current_step = 'complete' AND completed_at IS NOT NULL) OR "
            "(current_step <> 'complete' AND completed_at IS NULL)",
            name="ck_onboarding_state_completion",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO onboarding_state "
            "(id, current_step, step_statuses) "
            "VALUES ('default', 'storage_privacy', :statuses)"
        ).bindparams(statuses=_DEFAULT_STATUSES)
    )


def downgrade() -> None:
    op.drop_table("onboarding_state")
