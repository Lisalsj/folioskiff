"""Transactional access to durable jobs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.jobs.models import JobEventRecord, JobRecord, utc_now


class JobRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> JobRecord:
        with self._sessions() as session:
            if idempotency_key:
                existing = session.scalar(
                    select(JobRecord).where(JobRecord.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    session.expunge(existing)
                    return existing

            job = JobRecord(
                kind=kind,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            session.add(job)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if idempotency_key is None:
                    raise
                existing = session.scalar(
                    select(JobRecord).where(JobRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                session.expunge(existing)
                return existing
            session.refresh(job)
            session.expunge(job)
            return job

    def list_jobs(
        self, *, limit: int = 100, statuses: tuple[str, ...] | None = None
    ) -> list[JobRecord]:
        query = select(JobRecord)
        if statuses is not None:
            query = query.where(JobRecord.status.in_(statuses))
        with self._sessions() as session:
            jobs = list(
                session.scalars(
                    query.order_by(JobRecord.created_at.desc(), JobRecord.id.desc()).limit(limit)
                )
            )
            for job in jobs:
                session.expunge(job)
            return jobs

    def list_jobs_by_kind(self, *, kind: str) -> list[JobRecord]:
        with self._sessions() as session:
            jobs = list(
                session.scalars(
                    select(JobRecord)
                    .where(JobRecord.kind == kind)
                    .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                )
            )
            for job in jobs:
                session.expunge(job)
            return jobs

    def get(self, job_id: str) -> JobRecord | None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is not None:
                session.expunge(job)
            return job

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        with self._sessions() as session:
            job = session.scalar(
                select(JobRecord).where(JobRecord.idempotency_key == idempotency_key)
            )
            if job is not None:
                session.expunge(job)
            return job

    def list_resumable_ids(self, *, kind: str) -> tuple[str, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(JobRecord.id)
                    .where(
                        JobRecord.kind == kind,
                        JobRecord.status.in_(("queued", "running")),
                    )
                    .order_by(JobRecord.created_at, JobRecord.id)
                )
            )

    def update_state(
        self,
        job_id: str,
        *,
        status: str,
        progress: int,
        result: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        if not 0 <= progress <= 100:
            raise ValueError("Job progress must be between 0 and 100")
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            job.status = status
            job.progress = progress
            job.result = result
            job.checkpoint = checkpoint
            job.error_code = error_code
            job.error_message = error_message
            if status == "paused":
                job.paused_at = utc_now()
            elif status in {"queued", "running", "succeeded", "failed", "cancelled"}:
                job.paused_at = None
            session.commit()
            session.refresh(job)
            session.expunge(job)
            return job

    def request_cancel(self, job_id: str) -> JobRecord | None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return None
            if not job.cancel_requested:
                current_sequence = session.scalar(
                    select(func.max(JobEventRecord.sequence)).where(
                        JobEventRecord.job_id == job_id
                    )
                )
                job.cancel_requested = True
                session.add(
                    JobEventRecord(
                        job_id=job_id,
                        sequence=(current_sequence or 0) + 1,
                        event_type="job.cancel_requested",
                        progress=job.progress,
                        payload={},
                    )
                )
                session.commit()
                session.refresh(job)
            session.expunge(job)
            return job

    def request_pause(self, job_id: str) -> JobRecord | None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return None
            if job.status in {"queued", "running"}:
                job.pause_requested = True
                session.commit()
                session.refresh(job)
            session.expunge(job)
            return job

    def resume(self, job_id: str) -> JobRecord | None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                return None
            if job.status not in {"paused", "failed"}:
                raise ValueError("only a paused or retryable failed job can resume")
            job.status = "queued"
            job.pause_requested = False
            job.cancel_requested = False
            job.paused_at = None
            job.error_code = None
            job.error_message = None
            session.commit()
            session.refresh(job)
            session.expunge(job)
            return job

    def append_event(
        self,
        *,
        job_id: str,
        event_type: str,
        progress: int,
        payload: dict[str, Any],
    ) -> JobEventRecord:
        if not 0 <= progress <= 100:
            raise ValueError("Job progress must be between 0 and 100")

        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            current_sequence = session.scalar(
                select(func.max(JobEventRecord.sequence)).where(JobEventRecord.job_id == job_id)
            )
            event = JobEventRecord(
                job_id=job_id,
                sequence=(current_sequence or 0) + 1,
                event_type=event_type,
                progress=progress,
                payload=payload,
            )
            job.progress = progress
            session.add(event)
            session.commit()
            session.refresh(event)
            session.expunge(event)
            return event

    def list_events(
        self,
        *,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> list[JobEventRecord]:
        with self._sessions() as session:
            events = list(
                session.scalars(
                    select(JobEventRecord)
                    .where(JobEventRecord.event_id > after_event_id)
                    .order_by(JobEventRecord.event_id.asc())
                    .limit(limit)
                )
            )
            for event in events:
                session.expunge(event)
            return events
