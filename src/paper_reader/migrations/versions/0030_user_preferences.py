"""Persist singleton timezone and theme preferences.

Revision ID: 0030_user_preferences
Revises: 0029_recommendation_sets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_user_preferences"
down_revision: str | None = "0029_recommendation_sets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("timezone_mode", sa.String(length=16), nullable=False),
        sa.Column("timezone_name", sa.String(length=80), nullable=True),
        sa.Column("theme", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("id = 'default'", name="ck_user_preferences_singleton"),
        sa.CheckConstraint(
            "timezone_mode IN ('system', 'custom')",
            name="ck_user_preferences_timezone_mode",
        ),
        sa.CheckConstraint(
            "theme IN ('system', 'light', 'dark')",
            name="ck_user_preferences_theme",
        ),
        sa.CheckConstraint(
            "(timezone_mode = 'system' AND timezone_name IS NULL) OR "
            "(timezone_mode = 'custom' AND timezone_name IS NOT NULL "
            "AND length(timezone_name) > 0)",
            name="ck_user_preferences_timezone_value",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO user_preferences "
        "(id, timezone_mode, timezone_name, theme) "
        "VALUES ('default', 'system', NULL, 'system')"
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
