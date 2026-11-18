"""Add verified model asset lifecycle and recoverable job pause state.

Revision ID: 0026_model_assets
Revises: 0025_published_snapshots
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_model_assets"
down_revision: str | None = "0025_published_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "model_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("capability", sa.String(length=24), nullable=False),
        sa.Column("bundle_id", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("purpose", sa.String(length=1000), nullable=False),
        sa.Column("version", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.String(length=40), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="missing"),
        sa.Column("integrity", sa.String(length=24), nullable=False, server_default="missing"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('missing', 'downloading', 'paused', 'failed', 'ready')",
            name="ck_model_asset_status",
        ),
        sa.CheckConstraint(
            "integrity IN ('missing', 'partial', 'invalid', 'verified')",
            name="ck_model_asset_integrity",
        ),
        sa.CheckConstraint(
            "received_bytes >= 0 AND total_bytes > 0 AND received_bytes <= total_bytes",
            name="ck_model_asset_byte_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "revision", name="uq_model_asset_revision"),
    )
    op.create_index("ix_model_assets_capability", "model_assets", ["capability"])
    op.create_index("ix_model_assets_bundle_id", "model_assets", ["bundle_id"])
    op.create_index("ix_model_assets_status", "model_assets", ["status"])

    op.create_table(
        "active_model_assets",
        sa.Column("bundle_id", sa.String(length=120), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["model_assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("bundle_id"),
        sa.UniqueConstraint("asset_id"),
    )
    op.create_index("ix_active_model_assets_asset_id", "active_model_assets", ["asset_id"])

    op.create_table(
        "model_asset_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("reference_id", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('job', 'runtime', 'index')",
            name="ck_model_asset_reference_kind",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["model_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "kind", "reference_id", name="uq_model_asset_reference"),
    )
    op.create_index("ix_model_asset_references_asset_id", "model_asset_references", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_model_asset_references_asset_id", table_name="model_asset_references")
    op.drop_table("model_asset_references")
    op.drop_index("ix_active_model_assets_asset_id", table_name="active_model_assets")
    op.drop_table("active_model_assets")
    op.drop_index("ix_model_assets_status", table_name="model_assets")
    op.drop_index("ix_model_assets_bundle_id", table_name="model_assets")
    op.drop_index("ix_model_assets_capability", table_name="model_assets")
    op.drop_table("model_assets")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("paused_at")
        batch.drop_column("pause_requested")
