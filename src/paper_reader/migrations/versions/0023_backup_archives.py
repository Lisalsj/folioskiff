"""Persist verifiable backup packages and path-free restore receipts.

Revision ID: 0023_backup_archives
Revises: 0022_comparison_reports
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_backup_archives"
down_revision: str | None = "0022_comparison_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_archives",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("format_version", sa.String(length=80), nullable=False),
        sa.Column("app_version", sa.String(length=40), nullable=False),
        sa.Column("schema_revision", sa.String(length=100), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('manual', 'automatic')",
            name="ck_backup_archive_kind",
        ),
        sa.CheckConstraint("byte_size > 0", name="ck_backup_archive_byte_size"),
        sa.CheckConstraint("object_count >= 0", name="ck_backup_archive_object_count"),
        sa.CheckConstraint(
            "length(package_sha256) = 64 AND length(manifest_sha256) = 64",
            name="ck_backup_archive_hashes",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name="ck_backup_archive_deleted_after_created",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_backup_archive_job"),
        sa.UniqueConstraint("relative_path", name="uq_backup_archive_relative_path"),
    )
    op.create_index(
        "ix_backup_archives_kind_created_at",
        "backup_archives",
        ["kind", "created_at"],
    )
    op.create_index(
        "ix_backup_archives_package_sha256",
        "backup_archives",
        ["package_sha256"],
    )
    op.create_index(
        "ix_backup_archives_deleted_at",
        "backup_archives",
        ["deleted_at"],
    )

    op.create_table(
        "backup_schedule_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "restore_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_format_version", sa.String(length=80), nullable=False),
        sa.Column("source_app_version", sa.String(length=40), nullable=False),
        sa.Column("source_schema_revision", sa.String(length=100), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migrated_to_revision", sa.String(length=100), nullable=False),
        sa.CheckConstraint(
            "length(package_sha256) = 64",
            name="ck_restore_receipt_package_hash",
        ),
        sa.CheckConstraint("object_count >= 0", name="ck_restore_receipt_object_count"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_restore_receipts_package_sha256",
        "restore_receipts",
        ["package_sha256"],
    )
    op.create_index(
        "ix_restore_receipts_restored_at",
        "restore_receipts",
        ["restored_at"],
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER restore_receipts_immutable_{operation.casefold()} "
            f"BEFORE {operation} ON restore_receipts "
            "BEGIN SELECT RAISE(ABORT, 'restore receipt rows are immutable'); END"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS restore_receipts_immutable_delete")
    op.execute("DROP TRIGGER IF EXISTS restore_receipts_immutable_update")
    op.drop_index("ix_restore_receipts_restored_at", table_name="restore_receipts")
    op.drop_index("ix_restore_receipts_package_sha256", table_name="restore_receipts")
    op.drop_table("restore_receipts")
    op.drop_table("backup_schedule_state")
    op.drop_index("ix_backup_archives_deleted_at", table_name="backup_archives")
    op.drop_index("ix_backup_archives_package_sha256", table_name="backup_archives")
    op.drop_index("ix_backup_archives_kind_created_at", table_name="backup_archives")
    op.drop_table("backup_archives")
