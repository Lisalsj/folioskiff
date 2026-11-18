"""Transactional catalog for completed backup archives."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.backups.archive import BackupArchiveResult
from paper_reader.backups.models import BackupArchiveRecord, BackupScheduleStateRecord
from paper_reader.jobs.models import JobRecord


class BackupRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(
        self,
        *,
        job_id: str,
        kind: str,
        relative_path: str,
        byte_size: int,
        encrypted: bool,
        result: BackupArchiveResult,
    ) -> BackupArchiveRecord:
        record = BackupArchiveRecord(
            job_id=job_id,
            kind=kind,
            format_version=result.manifest.format_version,
            app_version=result.manifest.app_version,
            schema_revision=result.manifest.schema_revision,
            relative_path=relative_path,
            byte_size=byte_size,
            package_sha256=result.package_sha256,
            manifest_sha256=result.manifest_sha256,
            object_count=len(result.manifest.objects),
            encrypted=encrypted,
            created_at=result.manifest.created_at,
        )
        with self._sessions() as session:
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(BackupArchiveRecord).where(BackupArchiveRecord.job_id == job_id)
                )
                if existing is None:
                    raise
                session.expunge(existing)
                return existing
            session.refresh(record)
            session.expunge(record)
            return record

    def get(self, backup_id: str) -> BackupArchiveRecord | None:
        with self._sessions() as session:
            record = session.get(BackupArchiveRecord, backup_id)
            if record is not None:
                session.expunge(record)
            return record

    def get_by_job(self, job_id: str) -> BackupArchiveRecord | None:
        with self._sessions() as session:
            record = session.scalar(
                select(BackupArchiveRecord).where(BackupArchiveRecord.job_id == job_id)
            )
            if record is not None:
                session.expunge(record)
            return record

    def list(
        self,
        *,
        limit: int = 200,
        include_deleted: bool = False,
    ) -> tuple[BackupArchiveRecord, ...]:
        with self._sessions() as session:
            statement = select(BackupArchiveRecord)
            if not include_deleted:
                statement = statement.where(BackupArchiveRecord.deleted_at.is_(None))
            records = tuple(
                session.scalars(
                    statement.order_by(
                        BackupArchiveRecord.created_at.desc(),
                        BackupArchiveRecord.id.desc(),
                    ).limit(limit)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def latest_automatic(self) -> BackupArchiveRecord | None:
        with self._sessions() as session:
            record = session.scalar(
                select(BackupArchiveRecord)
                .join(JobRecord, JobRecord.id == BackupArchiveRecord.job_id)
                .where(
                    BackupArchiveRecord.kind == "automatic",
                    BackupArchiveRecord.deleted_at.is_(None),
                    JobRecord.status == "succeeded",
                )
                .order_by(
                    BackupArchiveRecord.created_at.desc(),
                    BackupArchiveRecord.id.desc(),
                )
                .limit(1)
            )
            if record is not None:
                session.expunge(record)
            return record

    def active_automatic_job(self) -> JobRecord | None:
        with self._sessions() as session:
            candidates = tuple(
                session.scalars(
                    select(JobRecord)
                    .where(
                        JobRecord.kind == "backup.create",
                        JobRecord.status.in_(("queued", "running")),
                    )
                    .order_by(JobRecord.created_at, JobRecord.id)
                )
            )
            record = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.payload.get("kind") == "automatic"
                    and candidate.payload.get("encrypted") is False
                ),
                None,
            )
            if record is not None:
                session.expunge(record)
            return record

    def schedule_started_at(self, *, default: datetime) -> datetime:
        with self._sessions() as session:
            record = session.get(BackupScheduleStateRecord, "weekly")
            if record is not None:
                return record.started_at
            record = BackupScheduleStateRecord(id="weekly", started_at=default)
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.get(BackupScheduleStateRecord, "weekly")
                if winner is None:
                    raise
                return winner.started_at
            return record.started_at

    def prunable_automatic(self, *, keep: int) -> tuple[BackupArchiveRecord, ...]:
        if keep < 1:
            raise ValueError("keep must be at least one")
        with self._sessions() as session:
            records = tuple(
                session.scalars(
                    select(BackupArchiveRecord)
                    .join(JobRecord, JobRecord.id == BackupArchiveRecord.job_id)
                    .where(
                        BackupArchiveRecord.kind == "automatic",
                        BackupArchiveRecord.deleted_at.is_(None),
                        JobRecord.status == "succeeded",
                    )
                    .order_by(
                        BackupArchiveRecord.created_at.desc(),
                        BackupArchiveRecord.id.desc(),
                    )
                    .offset(keep)
                )
            )
            for record in records:
                session.expunge(record)
            return records

    def mark_deleted(self, backup_id: str, *, deleted_at: datetime) -> BackupArchiveRecord:
        with self._sessions() as session:
            record = session.get(BackupArchiveRecord, backup_id)
            if record is None:
                raise KeyError(backup_id)
            if record.deleted_at is None:
                record.deleted_at = deleted_at
                session.commit()
                session.refresh(record)
            session.expunge(record)
            return record
