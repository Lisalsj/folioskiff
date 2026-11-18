"""Add public update metadata cache and automatic-check preference.

Revision ID: 0027_update_settings
Revises: 0026_model_assets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_update_settings"
down_revision: str | None = "0026_model_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "update_settings",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column(
            "automatic_checks_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_status",
            sa.String(length=32),
            nullable=False,
            server_default="never_checked",
        ),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("cached_release_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 'default'", name="ck_update_settings_singleton"),
        sa.CheckConstraint(
            "last_status IN ("
            "'never_checked', 'up_to_date', 'update_available', 'server_older', "
            "'prerelease', 'draft', 'rate_limited', 'offline', "
            "'invalid_response', 'service_unavailable'"
            ")",
            name="ck_update_settings_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("update_settings")
