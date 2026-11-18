"""Register discovered plugins and append-only permission decisions.

Revision ID: 0024_plugin_registry
Revises: 0023_backup_archives
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_plugin_registry"
down_revision: str | None = "0023_backup_archives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_installations",
        sa.Column("plugin_id", sa.String(length=80), nullable=False),
        sa.Column("entry_point_value", sa.String(length=255), nullable=False),
        sa.Column("distribution_name", sa.String(length=160), nullable=False),
        sa.Column("distribution_version", sa.String(length=80), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("protocol_minimum", sa.String(length=16), nullable=False),
        sa.Column("protocol_maximum", sa.String(length=16), nullable=False),
        sa.Column("compatible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("compatibility_reason", sa.String(length=100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("circuit_open_reason", sa.String(length=100), nullable=True),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_called_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_plugin_installation_manifest_hash",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_plugin_installation_failure_count",
        ),
        sa.CheckConstraint(
            "enabled = 0 OR compatible = 1",
            name="ck_plugin_installation_enabled_compatible",
        ),
        sa.PrimaryKeyConstraint("plugin_id"),
    )
    op.create_index(
        "ix_plugin_installations_distribution",
        "plugin_installations",
        ["distribution_name", "distribution_version"],
    )
    op.create_index(
        "ix_plugin_installations_enabled",
        "plugin_installations",
        ["enabled", "compatible"],
    )

    op.create_table(
        "plugin_permission_decisions",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plugin_id", sa.String(length=80), nullable=False),
        sa.Column("distribution_version", sa.String(length=80), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("permission_kind", sa.String(length=16), nullable=False),
        sa.Column("resource", sa.String(length=253), nullable=False),
        sa.Column("decision", sa.String(length=8), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_plugin_permission_manifest_hash",
        ),
        sa.CheckConstraint(
            "permission_kind IN ('network', 'file', 'credential')",
            name="ck_plugin_permission_kind",
        ),
        sa.CheckConstraint(
            "decision IN ('grant', 'revoke')",
            name="ck_plugin_permission_decision",
        ),
        sa.ForeignKeyConstraint(
            ["plugin_id"],
            ["plugin_installations.plugin_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id", name="uq_plugin_permission_decision_id"),
    )
    op.create_index(
        "ix_plugin_permission_current",
        "plugin_permission_decisions",
        ["plugin_id", "distribution_version", "manifest_sha256", "sequence"],
    )
    op.create_index(
        "ix_plugin_permission_resource",
        "plugin_permission_decisions",
        ["plugin_id", "permission_kind", "resource", "sequence"],
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER plugin_permission_decisions_immutable_{operation.casefold()} "
            f"BEFORE {operation} ON plugin_permission_decisions "
            "BEGIN SELECT RAISE(ABORT, 'plugin permission decisions are immutable'); END"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS plugin_permission_decisions_immutable_delete")
    op.execute("DROP TRIGGER IF EXISTS plugin_permission_decisions_immutable_update")
    op.drop_index("ix_plugin_permission_resource", table_name="plugin_permission_decisions")
    op.drop_index("ix_plugin_permission_current", table_name="plugin_permission_decisions")
    op.drop_table("plugin_permission_decisions")
    op.drop_index("ix_plugin_installations_enabled", table_name="plugin_installations")
    op.drop_index("ix_plugin_installations_distribution", table_name="plugin_installations")
    op.drop_table("plugin_installations")
