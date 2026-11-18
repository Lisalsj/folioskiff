"""Durable month-by-source discovery execution with immutable run snapshots."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.connectors import (
    ConnectorRegistry,
    ConnectorRequestError,
    SourceQuery,
)
from paper_reader.discovery.filtering import SourceFilterCounts, filter_source_records
from paper_reader.discovery.identity import DiscoveryKind, IdentityRepository, MatchKind
from paper_reader.discovery.keywords import CompiledKeywordQuery, KeywordRepository
from paper_reader.discovery.models import PlanRunRecord, RunBatchRecord, RunCandidateRecord
from paper_reader.discovery.plans import DiscoveryPlanRepository, ResolvedDateRange
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


MIN_DISCOVERY_SCAN_LIMIT = 50
DISCOVERY_SCAN_MULTIPLIER = 10
CONNECTOR_PAGE_LIMIT = 50
logger = logging.getLogger(__name__)
DISCOVERY_PRECEDENCE = {
    DiscoveryKind.UNCHANGED: 0,
    DiscoveryKind.EXISTING_WORK_NEW_SOURCE: 1,
    DiscoveryKind.METADATA_REFRESH: 2,
    DiscoveryKind.IMPORTANT_REVISION: 3,
    DiscoveryKind.NEW_WORK: 4,
}


@dataclass(frozen=True, slots=True)
class RunBatch:
    id: str
    run_id: str
    month: str
    connector_id: str
    status: str
    query_fingerprint: str
    checkpoint: dict[str, Any]
    fetched_count: int
    accepted_count: int
    ingested_count: int
    new_work_count: int
    update_count: int
    duplicate_count: int
    scanned_count: int
    attempt_count: int
    error_code: str | None
    error_message: str | None
    retryable: bool
    retry_after_seconds: float | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @property
    def filter_counts(self) -> SourceFilterCounts | None:
        return SourceFilterCounts.from_checkpoint(self.checkpoint)


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    id: str
    plan_id: str
    job_id: str
    keyword_version_id: str
    status: str
    months: tuple[str, ...]
    plan_snapshot: dict[str, Any]
    keyword_snapshot: dict[str, Any]
    connector_versions: dict[str, str]
    total_batches: int
    succeeded_batches: int
    failed_batches: int
    cancelled_batches: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    batches: tuple[RunBatch, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryRunPage:
    items: tuple[DiscoveryRun, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class RunBatchSpec:
    month: str
    connector_id: str
    query_fingerprint: str


@dataclass(frozen=True, slots=True)
class CandidateIngest:
    work_id: str
    source_record_id: str
    match_kind: MatchKind
    discovery_kind: DiscoveryKind


@dataclass(frozen=True, slots=True)
class RunCandidate:
    id: str
    run_id: str
    batch_id: str
    work_id: str
    source_record_id: str
    match_kind: str
    discovery_kind: str
    created_at: datetime


class DiscoveryRunRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_with_job(
        self,
        *,
        plan_id: str,
        keyword_version_id: str,
        months: tuple[str, ...],
        plan_snapshot: dict[str, Any],
        keyword_snapshot: dict[str, Any],
        connector_versions: dict[str, str],
        batches: tuple[RunBatchSpec, ...],
        job_payload: dict[str, Any],
        idempotency_key: str | None,
        created_at: datetime,
    ) -> DiscoveryRun:
        if not batches:
            raise ValueError("a discovery run requires at least one batch")
        with self._sessions() as session:
            if idempotency_key is not None:
                existing_job = session.scalar(
                    select(JobRecord).where(JobRecord.idempotency_key == idempotency_key)
                )
                if existing_job is not None:
                    existing_run = session.scalar(
                        select(PlanRunRecord).where(PlanRunRecord.job_id == existing_job.id)
                    )
                    if existing_run is None:
                        raise ValueError("idempotency key belongs to another job kind")
                    return self._to_domain(session, existing_run)

            job = JobRecord(
                kind="discovery.run",
                payload=job_payload,
                idempotency_key=idempotency_key,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(job)
            session.flush()
            record = PlanRunRecord(
                plan_id=plan_id,
                job_id=job.id,
                keyword_version_id=keyword_version_id,
                status="queued",
                months=list(months),
                plan_snapshot=plan_snapshot,
                keyword_snapshot=keyword_snapshot,
                connector_versions=connector_versions,
                total_batches=len(batches),
                succeeded_batches=0,
                failed_batches=0,
                cancelled_batches=0,
                created_at=created_at,
            )
            session.add(record)
            session.flush()
            session.add_all(
                RunBatchRecord(
                    run_id=record.id,
                    month=batch.month,
                    connector_id=batch.connector_id,
                    status="queued",
                    query_fingerprint=batch.query_fingerprint,
                    checkpoint={
                        "cursor": None,
                        "request_fingerprint": batch.query_fingerprint,
                    },
                    fetched_count=0,
                    accepted_count=0,
                    ingested_count=0,
                    new_work_count=0,
                    update_count=0,
                    duplicate_count=0,
                    scanned_count=0,
                    attempt_count=0,
                    retryable=False,
                    created_at=created_at,
                    updated_at=created_at,
                )
                for batch in batches
            )
            session.commit()
            return self._to_domain(session, record)

    def get(self, run_id: str) -> DiscoveryRun:
        with self._sessions() as session:
            record = session.get(PlanRunRecord, run_id)
            if record is None:
                raise KeyError(run_id)
            return self._to_domain(session, record)

    def list_for_plan(
        self,
        plan_id: str,
        *,
        limit: int,
        offset: int,
    ) -> DiscoveryRunPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        with self._sessions() as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(PlanRunRecord)
                    .where(PlanRunRecord.plan_id == plan_id)
                )
                or 0
            )
            records = tuple(
                session.scalars(
                    select(PlanRunRecord)
                    .where(PlanRunRecord.plan_id == plan_id)
                    .order_by(PlanRunRecord.created_at.desc(), PlanRunRecord.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            items = tuple(self._to_domain(session, record) for record in records)
        return DiscoveryRunPage(items=items, total=total, limit=limit, offset=offset)

    def list_resumable_ids(self) -> tuple[str, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(PlanRunRecord.id)
                    .where(PlanRunRecord.status.in_(("queued", "running")))
                    .order_by(PlanRunRecord.created_at)
                )
            )

    def record_candidates(
        self,
        *,
        run_id: str,
        batch_id: str,
        candidates: tuple[CandidateIngest, ...],
        now: datetime,
    ) -> None:
        if not candidates:
            return
        unique_candidates = self._unique_candidates(candidates)
        with self._sessions() as session:
            batch = session.get(RunBatchRecord, batch_id)
            if batch is None or batch.run_id != run_id:
                raise ValueError("batch_id must belong to run_id")
            for candidate in unique_candidates:
                record = session.scalar(
                    select(RunCandidateRecord).where(
                        RunCandidateRecord.run_id == run_id,
                        RunCandidateRecord.source_record_id == candidate.source_record_id,
                    )
                )
                if record is None:
                    session.add(
                        RunCandidateRecord(
                            run_id=run_id,
                            batch_id=batch_id,
                            work_id=candidate.work_id,
                            source_record_id=candidate.source_record_id,
                            match_kind=candidate.match_kind.value,
                            discovery_kind=candidate.discovery_kind.value,
                            created_at=now,
                        )
                    )
            session.commit()

    def list_candidates(self, run_id: str) -> tuple[RunCandidate, ...]:
        with self._sessions() as session:
            records = session.scalars(
                select(RunCandidateRecord)
                .where(RunCandidateRecord.run_id == run_id)
                .order_by(RunCandidateRecord.created_at, RunCandidateRecord.id)
            )
            return tuple(
                RunCandidate(
                    id=record.id,
                    run_id=record.run_id,
                    batch_id=record.batch_id,
                    work_id=record.work_id,
                    source_record_id=record.source_record_id,
                    match_kind=record.match_kind,
                    discovery_kind=record.discovery_kind,
                    created_at=_utc(record.created_at),
                )
                for record in records
            )

    def list_candidate_work_ids(self, run_id: str) -> tuple[str, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(RunCandidateRecord.work_id)
                    .where(RunCandidateRecord.run_id == run_id)
                    .distinct()
                    .order_by(RunCandidateRecord.work_id)
                )
            )

    def start_run(self, run_id: str, *, now: datetime) -> DiscoveryRun:
        with self._sessions() as session:
            record = self._require_run(session, run_id)
            record.status = "running"
            record.started_at = record.started_at or now
            record.completed_at = None
            session.commit()
            return self._to_domain(session, record)

    def start_batch(self, batch_id: str, *, now: datetime) -> RunBatch:
        with self._sessions() as session:
            record = self._require_batch(session, batch_id)
            record.status = "running"
            record.attempt_count += 1
            record.error_code = None
            record.error_message = None
            record.retryable = False
            record.retry_after_seconds = None
            record.completed_at = None
            record.updated_at = now
            session.commit()
            return self._batch_domain(record)

    def save_page(
        self,
        batch_id: str,
        *,
        page_cursor: str | None,
        next_cursor: str | None,
        fetched: int,
        accepted: int,
        ingested: int,
        candidates: tuple[CandidateIngest, ...],
        now: datetime,
        filter_counts: SourceFilterCounts | None = None,
    ) -> RunBatch:
        with self._sessions() as session:
            record = self._require_batch(session, batch_id)
            page_identity = page_cursor if page_cursor is not None else "__first_page__"
            if record.checkpoint.get("last_page_cursor") == page_identity:
                return self._batch_domain(record)

            new_work_ids = set(
                session.scalars(
                    select(RunCandidateRecord.work_id).where(
                        RunCandidateRecord.run_id == record.run_id,
                        RunCandidateRecord.discovery_kind == DiscoveryKind.NEW_WORK.value,
                    )
                )
            )
            new_work_delta = 0
            update_delta = 0
            duplicate_delta = 0
            for candidate in candidates:
                if candidate.discovery_kind is DiscoveryKind.NEW_WORK:
                    if candidate.work_id in new_work_ids:
                        duplicate_delta += 1
                    else:
                        new_work_ids.add(candidate.work_id)
                        new_work_delta += 1
                elif candidate.discovery_kind in {
                    DiscoveryKind.METADATA_REFRESH,
                    DiscoveryKind.IMPORTANT_REVISION,
                }:
                    update_delta += 1
                else:
                    duplicate_delta += 1

            for candidate in self._unique_candidates(candidates):
                existing = session.scalar(
                    select(RunCandidateRecord).where(
                        RunCandidateRecord.run_id == record.run_id,
                        RunCandidateRecord.source_record_id == candidate.source_record_id,
                    )
                )
                if existing is None:
                    session.add(
                        RunCandidateRecord(
                            run_id=record.run_id,
                            batch_id=batch_id,
                            work_id=candidate.work_id,
                            source_record_id=candidate.source_record_id,
                            match_kind=candidate.match_kind.value,
                            discovery_kind=candidate.discovery_kind.value,
                            created_at=now,
                        )
                    )

            record.fetched_count += fetched
            record.accepted_count += accepted
            record.ingested_count += ingested
            record.new_work_count += new_work_delta
            record.update_count += update_delta
            record.duplicate_count += duplicate_delta
            record.scanned_count += fetched
            checkpoint = {
                **record.checkpoint,
                "cursor": next_cursor,
                "request_fingerprint": record.query_fingerprint,
                "last_page_cursor": page_identity,
                "last_success_at": now.isoformat(),
            }
            if filter_counts is not None:
                previous = SourceFilterCounts.from_checkpoint(record.checkpoint)
                checkpoint["filter_counts"] = (
                    (previous or SourceFilterCounts()).add(filter_counts).to_dict()
                )
            record.checkpoint = checkpoint
            record.updated_at = now
            session.commit()
            return self._batch_domain(record)

    @staticmethod
    def _unique_candidates(
        candidates: tuple[CandidateIngest, ...],
    ) -> tuple[CandidateIngest, ...]:
        selected: dict[str, CandidateIngest] = {}
        for candidate in candidates:
            current = selected.get(candidate.source_record_id)
            if current is None or (
                DISCOVERY_PRECEDENCE[candidate.discovery_kind]
                > DISCOVERY_PRECEDENCE[current.discovery_kind]
            ):
                selected[candidate.source_record_id] = candidate
        return tuple(selected.values())

    def succeed_batch(
        self,
        batch_id: str,
        *,
        now: datetime,
        stop_reason: str | None = None,
    ) -> RunBatch:
        with self._sessions() as session:
            record = self._require_batch(session, batch_id)
            record.status = "succeeded"
            if stop_reason is not None:
                checkpoint = dict(record.checkpoint)
                checkpoint["stop_reason"] = stop_reason
                record.checkpoint = checkpoint
            record.completed_at = now
            record.updated_at = now
            record.retryable = False
            session.commit()
            return self._batch_domain(record)

    def fail_batch(
        self,
        batch_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_after_seconds: float | None,
        now: datetime,
    ) -> RunBatch:
        with self._sessions() as session:
            record = self._require_batch(session, batch_id)
            record.status = "failed"
            record.error_code = error_code
            record.error_message = error_message
            record.retryable = retryable
            record.retry_after_seconds = retry_after_seconds
            record.completed_at = now
            record.updated_at = now
            session.commit()
            return self._batch_domain(record)

    def cancel_open_batches(self, run_id: str, *, now: datetime) -> None:
        with self._sessions() as session:
            records = session.scalars(
                select(RunBatchRecord).where(
                    RunBatchRecord.run_id == run_id,
                    RunBatchRecord.status.in_(("queued", "running")),
                )
            )
            for record in records:
                record.status = "cancelled"
                record.completed_at = now
                record.updated_at = now
            session.commit()

    def finish_run(self, run_id: str, *, status: str, now: datetime) -> DiscoveryRun:
        with self._sessions() as session:
            record = self._require_run(session, run_id)
            batches = list(
                session.scalars(select(RunBatchRecord).where(RunBatchRecord.run_id == run_id))
            )
            record.status = status
            record.succeeded_batches = sum(batch.status == "succeeded" for batch in batches)
            record.failed_batches = sum(batch.status == "failed" for batch in batches)
            record.cancelled_batches = sum(batch.status == "cancelled" for batch in batches)
            record.completed_at = now
            session.commit()
            return self._to_domain(session, record, batches=batches)

    @staticmethod
    def _require_run(session: Session, run_id: str) -> PlanRunRecord:
        record = session.get(PlanRunRecord, run_id)
        if record is None:
            raise KeyError(run_id)
        return record

    @staticmethod
    def _require_batch(session: Session, batch_id: str) -> RunBatchRecord:
        record = session.get(RunBatchRecord, batch_id)
        if record is None:
            raise KeyError(batch_id)
        return record

    @classmethod
    def _to_domain(
        cls,
        session: Session,
        record: PlanRunRecord,
        *,
        batches: list[RunBatchRecord] | None = None,
    ) -> DiscoveryRun:
        batch_records = (
            sorted(batches, key=lambda batch: (batch.month, batch.connector_id))
            if batches is not None
            else list(
                session.scalars(
                    select(RunBatchRecord)
                    .where(RunBatchRecord.run_id == record.id)
                    .order_by(RunBatchRecord.month, RunBatchRecord.connector_id)
                )
            )
        )
        return DiscoveryRun(
            id=record.id,
            plan_id=record.plan_id,
            job_id=record.job_id,
            keyword_version_id=record.keyword_version_id,
            status=record.status,
            months=tuple(record.months),
            plan_snapshot=dict(record.plan_snapshot),
            keyword_snapshot=dict(record.keyword_snapshot),
            connector_versions=dict(record.connector_versions),
            total_batches=record.total_batches,
            succeeded_batches=record.succeeded_batches,
            failed_batches=record.failed_batches,
            cancelled_batches=record.cancelled_batches,
            created_at=_utc(record.created_at),
            started_at=_utc(record.started_at) if record.started_at is not None else None,
            completed_at=(_utc(record.completed_at) if record.completed_at is not None else None),
            batches=tuple(cls._batch_domain(batch) for batch in batch_records),
        )

    @staticmethod
    def _batch_domain(record: RunBatchRecord) -> RunBatch:
        return RunBatch(
            id=record.id,
            run_id=record.run_id,
            month=record.month,
            connector_id=record.connector_id,
            status=record.status,
            query_fingerprint=record.query_fingerprint,
            checkpoint=dict(record.checkpoint),
            fetched_count=record.fetched_count,
            accepted_count=record.accepted_count,
            ingested_count=record.ingested_count,
            new_work_count=record.new_work_count,
            update_count=record.update_count,
            duplicate_count=record.duplicate_count,
            scanned_count=record.scanned_count,
            attempt_count=record.attempt_count,
            error_code=record.error_code,
            error_message=record.error_message,
            retryable=record.retryable,
            retry_after_seconds=record.retry_after_seconds,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
            completed_at=(_utc(record.completed_at) if record.completed_at is not None else None),
        )


class _CancellationRequested(RuntimeError):
    pass


class DiscoveryRunService:
    def __init__(
        self,
        *,
        plans: DiscoveryPlanRepository,
        keywords: KeywordRepository,
        connectors: ConnectorRegistry,
        identities: IdentityRepository,
        jobs: JobRepository,
        runs: DiscoveryRunRepository,
    ) -> None:
        self._plans = plans
        self._keywords = keywords
        self._connectors = connectors
        self._identities = identities
        self._jobs = jobs
        self._runs = runs

    def create(
        self,
        plan_id: str,
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> DiscoveryRun:
        created_at = _utc(now or datetime.now(UTC))
        plan = self._plans.get(plan_id)
        resolved = plan.date_range.resolve(created_at)
        keyword_version = self._keywords.get(plan.keyword_version_id)
        compiled = self._keywords.compile(plan.keyword_version_id)
        connector_versions = {
            source: (
                connector.capabilities.protocol_version
                if (connector := self._connectors.get(source)) is not None
                else "unavailable"
            )
            for source in plan.sources
        }
        quota_snapshot = plan.quota.model_dump(mode="json")
        quota_snapshot["scan_limit"] = max(
            MIN_DISCOVERY_SCAN_LIMIT,
            plan.quota.candidate_limit * DISCOVERY_SCAN_MULTIPLIER,
        )
        plan_snapshot: dict[str, Any] = {
            "name": plan.name,
            "research_intent": plan.research_intent,
            "date_range": plan.date_range.model_dump(mode="json"),
            "resolved_months": list(resolved.months),
            "date_warnings": list(resolved.warnings),
            "sources": list(plan.sources),
            "source_filters": plan.source_filters,
            "schedule": plan.schedule.model_dump(mode="json"),
            "quota": quota_snapshot,
        }
        keyword_snapshot: dict[str, Any] = {
            "version_id": keyword_version.id,
            "research_intent": keyword_version.research_intent,
            "definition": keyword_version.definition.model_dump(mode="json"),
            "boolean_expression": compiled.boolean_expression,
            "required_groups": [list(group) for group in compiled.required_groups],
            "boost_terms": list(compiled.boost_terms),
            "excluded_groups": [list(group) for group in compiled.excluded_groups],
            "generator_info": keyword_version.generator_info,
            "confirmed_at": (
                keyword_version.confirmed_at.isoformat()
                if keyword_version.confirmed_at is not None
                else None
            ),
        }
        batches = tuple(
            RunBatchSpec(
                month=month,
                connector_id=source,
                query_fingerprint=self._fingerprint(
                    plan_id=plan.id,
                    month=month,
                    connector_id=source,
                    keyword_version_id=compiled.version_id,
                    expression=compiled.boolean_expression,
                    filters=plan.source_filters.get(source, {}),
                ),
            )
            for month in resolved.months
            for source in plan.sources
        )
        namespaced_key = f"discovery.run:{idempotency_key}" if idempotency_key is not None else None
        return self._runs.create_with_job(
            plan_id=plan.id,
            keyword_version_id=keyword_version.id,
            months=resolved.months,
            plan_snapshot=plan_snapshot,
            keyword_snapshot=keyword_snapshot,
            connector_versions=connector_versions,
            batches=batches,
            job_payload={
                "plan_id": plan.id,
                "keyword_version_id": keyword_version.id,
                "months": list(resolved.months),
            },
            idempotency_key=namespaced_key,
            created_at=created_at,
        )

    async def execute(self, run_id: str) -> DiscoveryRun:
        return await self._execute(run_id, retry_failed=False)

    async def retry_failed(self, run_id: str) -> DiscoveryRun:
        return await self._execute(run_id, retry_failed=True)

    async def _execute(self, run_id: str, *, retry_failed: bool) -> DiscoveryRun:
        run = self._runs.get(run_id)
        if run.status in {"succeeded", "cancelled"}:
            return run
        now = datetime.now(UTC)
        run = self._runs.start_run(run.id, now=now)
        self._jobs.update_state(
            run.job_id,
            status="running",
            progress=self._progress(run),
            checkpoint=self._job_checkpoint(run),
        )
        self._jobs.append_event(
            job_id=run.job_id,
            event_type="discovery.run.started",
            progress=self._progress(run),
            payload={"run_id": run.id, "retry": retry_failed},
        )

        cancelled = False
        for batch in run.batches:
            if batch.status in {"succeeded", "cancelled"}:
                continue
            if batch.status == "failed" and not (retry_failed and batch.retryable):
                continue
            if self._cancel_requested(run.job_id):
                cancelled = True
                self._runs.cancel_open_batches(run.id, now=datetime.now(UTC))
                break
            try:
                await self._execute_batch(run, batch)
            except _CancellationRequested:
                cancelled = True
                self._runs.cancel_open_batches(run.id, now=datetime.now(UTC))
                break
            except ConnectorRequestError as error:
                failed = self._runs.fail_batch(
                    batch.id,
                    error_code=error.code,
                    error_message=str(error),
                    retryable=error.retryable,
                    retry_after_seconds=error.retry_after_seconds,
                    now=datetime.now(UTC),
                )
                self._append_batch_event(run, failed, "discovery.batch.failed")
            except Exception as error:  # connector isolation boundary
                failed = self._runs.fail_batch(
                    batch.id,
                    error_code="connector_internal_error",
                    error_message=type(error).__name__,
                    retryable=False,
                    retry_after_seconds=None,
                    now=datetime.now(UTC),
                )
                self._append_batch_event(run, failed, "discovery.batch.failed")

        latest = self._runs.get(run.id)
        status = self._final_status(latest, cancelled=cancelled)
        completed = self._runs.finish_run(run.id, status=status, now=datetime.now(UTC))
        error_code = "discovery_run_failed" if status == "failed" else None
        self._jobs.update_state(
            run.job_id,
            status=status,
            progress=100,
            result={
                "run_id": completed.id,
                "succeeded_batches": completed.succeeded_batches,
                "failed_batches": completed.failed_batches,
                "cancelled_batches": completed.cancelled_batches,
            },
            checkpoint=self._job_checkpoint(completed),
            error_code=error_code,
            error_message="No discovery batch succeeded" if error_code else None,
        )
        self._jobs.append_event(
            job_id=run.job_id,
            event_type=f"discovery.run.{status}",
            progress=100,
            payload={"run_id": completed.id},
        )
        return completed

    async def _execute_batch(self, run: DiscoveryRun, batch: RunBatch) -> None:
        connector = self._connectors.get(batch.connector_id)
        if connector is None:
            raise ConnectorRequestError(
                "connector_not_installed",
                f"Connector {batch.connector_id} is not installed",
                retryable=False,
            )
        if not connector.capabilities.enabled:
            raise ConnectorRequestError(
                connector.capabilities.disabled_reason or "connector_disabled",
                f"Connector {batch.connector_id} is disabled",
                retryable=False,
            )
        active = self._runs.start_batch(batch.id, now=datetime.now(UTC))
        compiled = self._compiled(run.keyword_snapshot)
        filters_value = run.plan_snapshot.get("source_filters", {})
        filters = (
            dict(filters_value.get(batch.connector_id, {}))
            if isinstance(filters_value, dict)
            else {}
        )
        query = SourceQuery(
            connector_id=batch.connector_id,
            months=(batch.month,),
            compiled_keywords=compiled,
            filters=filters,
            fingerprint=batch.query_fingerprint,
        )
        timezone = str(run.plan_snapshot["date_range"]["timezone"])
        resolved = ResolvedDateRange(timezone, (batch.month,), ())
        quota = int(run.plan_snapshot["quota"]["candidate_limit"])
        scan_limit = int(run.plan_snapshot["quota"]["scan_limit"])
        cursor_value = active.checkpoint.get("cursor")
        cursor = str(cursor_value) if cursor_value is not None else None

        while active.new_work_count < quota and active.scanned_count < scan_limit:
            if self._cancel_requested(run.job_id):
                raise _CancellationRequested
            remaining_scan = scan_limit - active.scanned_count
            page_limit = min(CONNECTOR_PAGE_LIMIT, remaining_scan)
            page = await connector.fetch_page(query, cursor=cursor, limit=page_limit)
            if self._cancel_requested(run.job_id):
                raise _CancellationRequested
            records = page.records[:page_limit]
            matched, filter_counts = filter_source_records(
                records, resolved=resolved, compiled=compiled
            )
            ingested = 0
            candidates: list[CandidateIngest] = []
            for paper in matched:
                result = self._identities.ingest(paper, run_id=run.id)
                candidates.append(
                    CandidateIngest(
                        work_id=result.work.id,
                        source_record_id=result.source_record.id,
                        match_kind=result.match_kind,
                        discovery_kind=result.discovery_kind,
                    )
                )
                if result.match_kind is not MatchKind.EXISTING_SOURCE:
                    ingested += 1
            checkpoint_at = datetime.now(UTC)
            active = self._runs.save_page(
                batch.id,
                page_cursor=cursor,
                next_cursor=page.next_cursor,
                fetched=len(records),
                accepted=len(matched),
                ingested=ingested,
                candidates=tuple(candidates),
                now=checkpoint_at,
                filter_counts=filter_counts,
            )
            self._append_batch_event(run, active, "discovery.batch.checkpoint")
            stop_reason: str | None = None
            if active.new_work_count >= quota:
                stop_reason = "new_work_quota"
            elif active.scanned_count >= scan_limit:
                stop_reason = "scan_limit"
            elif page.next_cursor is None or not records:
                stop_reason = "source_exhausted"
            if stop_reason is not None:
                succeeded = self._runs.succeed_batch(
                    batch.id,
                    now=datetime.now(UTC),
                    stop_reason=stop_reason,
                )
                self._append_batch_event(run, succeeded, "discovery.batch.succeeded")
                return
            cursor = page.next_cursor

        stop_reason = "new_work_quota" if active.new_work_count >= quota else "scan_limit"
        succeeded = self._runs.succeed_batch(
            batch.id,
            now=datetime.now(UTC),
            stop_reason=stop_reason,
        )
        self._append_batch_event(run, succeeded, "discovery.batch.succeeded")

    def _append_batch_event(
        self,
        run: DiscoveryRun,
        batch: RunBatch,
        event_type: str,
    ) -> None:
        current = self._runs.get(run.id)
        self._jobs.append_event(
            job_id=run.job_id,
            event_type=event_type,
            progress=self._progress(current),
            payload={
                "run_id": run.id,
                "batch_id": batch.id,
                "connector_id": batch.connector_id,
                "month": batch.month,
                "status": batch.status,
                "fetched": batch.fetched_count,
                "accepted": batch.accepted_count,
                "new_works": batch.new_work_count,
                "updates": batch.update_count,
                "duplicates": batch.duplicate_count,
                "scanned": batch.scanned_count,
                "filter_counts": batch.filter_counts.to_dict() if batch.filter_counts else None,
                "error_code": batch.error_code,
            },
        )
        if batch.status in {"succeeded", "failed"}:
            logger.info(
                "discovery batch %s source=%s month=%s fetched=%d accepted=%d "
                "stop_reason=%s error_code=%s",
                batch.status,
                batch.connector_id,
                batch.month,
                batch.fetched_count,
                batch.accepted_count,
                batch.checkpoint.get("stop_reason"),
                batch.error_code,
            )

    def _cancel_requested(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job.cancel_requested

    @staticmethod
    def _compiled(snapshot: dict[str, Any]) -> CompiledKeywordQuery:
        return CompiledKeywordQuery(
            version_id=str(snapshot["version_id"]),
            boolean_expression=str(snapshot["boolean_expression"]),
            required_groups=tuple(
                tuple(str(term) for term in group) for group in snapshot["required_groups"]
            ),
            boost_terms=tuple(str(term) for term in snapshot["boost_terms"]),
            excluded_groups=tuple(
                tuple(str(term) for term in group) for group in snapshot["excluded_groups"]
            ),
        )

    @staticmethod
    def _final_status(run: DiscoveryRun, *, cancelled: bool) -> str:
        if cancelled:
            return "cancelled"
        statuses = {batch.status for batch in run.batches}
        if statuses == {"succeeded"}:
            return "succeeded"
        if "succeeded" in statuses and "failed" in statuses:
            return "partial_success"
        if "failed" in statuses:
            return "failed"
        if "cancelled" in statuses:
            return "cancelled"
        return "failed"

    @staticmethod
    def _progress(run: DiscoveryRun) -> int:
        terminal = sum(
            batch.status in {"succeeded", "failed", "cancelled"} for batch in run.batches
        )
        return int((terminal * 100) / run.total_batches)

    @staticmethod
    def _job_checkpoint(run: DiscoveryRun) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "batches": {
                batch.id: {
                    "status": batch.status,
                    "cursor": batch.checkpoint.get("cursor"),
                }
                for batch in run.batches
            },
        }

    @staticmethod
    def _fingerprint(
        *,
        plan_id: str,
        month: str,
        connector_id: str,
        keyword_version_id: str,
        expression: str,
        filters: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            {
                "plan_id": plan_id,
                "month": month,
                "connector_id": connector_id,
                "keyword_version_id": keyword_version_id,
                "expression": expression,
                "filters": filters,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
