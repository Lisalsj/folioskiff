"""Database records for portable backups and completed restores."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from paper_reader.database import Base
from paper_reader.jobs.models import utc_now


class BackupArchiveRecord(Base):
    __tablename__ = "backup_archives"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('manual', 'automatic')",
            name="ck_backup_archive_kind",
        ),
        CheckConstraint("byte_size > 0", name="ck_backup_archive_byte_size"),
        CheckConstraint("object_count >= 0", name="ck_backup_archive_object_count"),
        CheckConstraint(
            "length(package_sha256) = 64 AND length(manifest_sha256) = 64",
            name="ck_backup_archive_hashes",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name="ck_backup_archive_deleted_after_created",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)
    format_version: Mapped[str] = mapped_column(String(80))
    app_version: Mapped[str] = mapped_column(String(40))
    schema_revision: Mapped[str] = mapped_column(String(100))
    relative_path: Mapped[str] = mapped_column(String(512), unique=True)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    package_sha256: Mapped[str] = mapped_column(String(64), index=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    object_count: Mapped[int] = mapped_column(Integer)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupScheduleStateRecord(Base):
    __tablename__ = "backup_schedule_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RestoreReceiptRecord(Base):
    __tablename__ = "restore_receipts"
    __table_args__ = (
        CheckConstraint(
            "length(package_sha256) = 64",
            name="ck_restore_receipt_package_hash",
        ),
        CheckConstraint("object_count >= 0", name="ck_restore_receipt_object_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    package_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_format_version: Mapped[str] = mapped_column(String(80))
    source_app_version: Mapped[str] = mapped_column(String(40))
    source_schema_revision: Mapped[str] = mapped_column(String(100))
    object_count: Mapped[int] = mapped_column(Integer)
    restored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    migrated_to_revision: Mapped[str] = mapped_column(String(100))
