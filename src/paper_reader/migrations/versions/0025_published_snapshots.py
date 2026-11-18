"""Freeze privacy-reviewed publication payloads and rendered files.

Revision ID: 0025_published_snapshots
Revises: 0024_plugin_registry
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_published_snapshots"
down_revision: str | None = "0024_plugin_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "published_snapshots",
    "published_snapshot_works",
    "published_snapshot_files",
)


def upgrade() -> None:
    op.create_table(
        "published_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("preview_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("selection_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(slug) >= 1 AND length(slug) <= 80",
            name="ck_published_snapshot_slug_length",
        ),
        sa.CheckConstraint(
            "length(preview_fingerprint) = 64 AND length(payload_sha256) = 64 "
            "AND length(manifest_sha256) = 64 AND length(snapshot_sha256) = 64",
            name="ck_published_snapshot_hashes",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_sha256", name="uq_published_snapshot_hash"),
    )
    op.create_index(
        "ix_published_snapshots_slug_created",
        "published_snapshots",
        ["slug", "created_at"],
    )
    op.create_index(
        "ix_published_snapshots_preview_fingerprint",
        "published_snapshots",
        ["preview_fingerprint"],
    )

    op.create_table(
        "published_snapshot_works",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("canonical_work_id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=True),
        sa.Column("score_snapshot_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint("position >= 0", name="ck_published_snapshot_work_position"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["published_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_work_id"],
            ["works.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["analysis_reports.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["score_snapshot_id"],
            ["score_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "position"),
        sa.UniqueConstraint(
            "snapshot_id",
            "canonical_work_id",
            name="uq_published_snapshot_canonical_work",
        ),
    )
    op.create_index(
        "ix_published_snapshot_works_canonical_work",
        "published_snapshot_works",
        ["canonical_work_id"],
    )
    op.create_index(
        "ix_published_snapshot_works_report",
        "published_snapshot_works",
        ["report_id"],
    )
    op.create_index(
        "ix_published_snapshot_works_score",
        "published_snapshot_works",
        ["score_snapshot_id"],
    )

    op.create_table(
        "published_snapshot_files",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_published_snapshot_file_position"),
        sa.CheckConstraint("byte_size >= 0", name="ck_published_snapshot_file_size"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_published_snapshot_file_hash"),
        sa.CheckConstraint(
            "relative_path IN ('index.html', 'data.json', 'manifest.json')",
            name="ck_published_snapshot_file_path",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["published_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "relative_path"),
        sa.UniqueConstraint(
            "snapshot_id",
            "position",
            name="uq_published_snapshot_file_position",
        ),
    )

    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END"
        )


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
    op.drop_table("published_snapshot_files")
    op.drop_index("ix_published_snapshot_works_score", table_name="published_snapshot_works")
    op.drop_index("ix_published_snapshot_works_report", table_name="published_snapshot_works")
    op.drop_index(
        "ix_published_snapshot_works_canonical_work",
        table_name="published_snapshot_works",
    )
    op.drop_table("published_snapshot_works")
    op.drop_index(
        "ix_published_snapshots_preview_fingerprint",
        table_name="published_snapshots",
    )
    op.drop_index("ix_published_snapshots_slug_created", table_name="published_snapshots")
    op.drop_table("published_snapshots")
