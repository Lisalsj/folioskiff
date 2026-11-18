"""Durable, resumable metadata enrichment for one discovery run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import SourceRecord, WorkRecord
from paper_reader.discovery.runs import DiscoveryRun
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.enrichers.registry import EnricherRegistry
from paper_reader.recommendation.enrichment import EnricherRequestError, WorkLookup
from paper_reader.recommendation.observations import MetadataObservationRepository

ENRICHMENT_JOB_KIND = "recommendation.metadata_enrichment"


class RunCandidates(Protocol):
    def get(self, run_id: str) -> DiscoveryRun: ...

    def list_candidate_work_ids(self, run_id: str) -> tuple[str, ...]: ...


class WorkLookupRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, work_id: str) -> WorkLookup:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            records = session.scalars(
                select(SourceRecord)
                .where(SourceRecord.work_id == work.id)
                .order_by(SourceRecord.fetched_at.desc(), SourceRecord.id)
            )
            source_ids: dict[str, str] = {}
            for record in records:
                source_ids.setdefault(record.connector_id, record.external_id)
            return WorkLookup(
                work_id=work.id,
                doi=work.doi,
                title=work.canonical_title,
                authors=tuple(work.authors),
                source_ids=source_ids,
            )

    @staticmethod
    def _canonical_work(session: Session, work_id: str) -> WorkRecord:
        work = session.get(WorkRecord, work_id)
        if work is None:
            raise KeyError(work_id)
        visited: set[str] = set()
        while work.status == "merged":
            if work.id in visited or work.merged_into_work_id is None:
                raise RuntimeError("Invalid Work merge chain")
            visited.add(work.id)
            target = session.get(WorkRecord, work.merged_into_work_id)
            if target is None:
                raise RuntimeError("Merged Work target is missing")
            work = target
        return work


class MetadataEnrichmentService:
    def __init__(
        self,
        *,
        runs: RunCandidates,
        jobs: JobRepository,
        lookups: WorkLookupRepository,
        observations: MetadataObservationRepository,
        enrichers: EnricherRegistry,
    ) -> None:
        self._runs = runs
        self._jobs = jobs
        self.lookups = lookups
        self._observations = observations
        self._enrichers = enrichers

    def create_job(
        self,
        run_id: str,
        *,
        enricher_ids: tuple[str, ...] | None = None,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        run = self._runs.get(run_id)
        if run.status not in {"succeeded", "partial_success"}:
            raise ValueError("discovery run must be terminal before metadata enrichment")
        selected_ids = (
            tuple(item.capabilities.enricher_id for item in self._enrichers.all())
            if enricher_ids is None
            else tuple(dict.fromkeys(enricher_ids))
        )
        if not selected_ids:
            raise ValueError("at least one metadata enricher is required")
        selected = []
        for enricher_id in selected_ids:
            enricher = self._enrichers.get(enricher_id)
            if enricher is None:
                raise KeyError(enricher_id)
            selected.append(
                {
                    "enricher_id": enricher.capabilities.enricher_id,
                    "version": enricher.capabilities.version,
                }
            )
        work_ids = tuple(dict.fromkeys(self._runs.list_candidate_work_ids(run_id)))
        payload: dict[str, Any] = {
            "run_id": run_id,
            "work_ids": list(work_ids),
            "enrichers": selected,
            "total": len(work_ids) * len(selected),
        }
        job = self._jobs.create(
            kind=ENRICHMENT_JOB_KIND,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if job.kind != ENRICHMENT_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different enrichment command")
        return job

    async def execute(self, job_id: str) -> None:
        job = self._require_job(job_id)
        tasks = self._tasks(job.payload)
        checkpoint = self._read_checkpoint(job.checkpoint)
        completed = checkpoint["completed"]
        failures = checkpoint["failures"]
        if job.cancel_requested:
            self._finish_cancelled(job.id, len(tasks), completed, failures)
            return

        self._jobs.update_state(
            job.id,
            status="running",
            progress=self._resume_progress(len(tasks), completed, failures),
            checkpoint=checkpoint,
        )
        for work_id, enricher_id, expected_version in tasks:
            key = self._task_key(work_id, enricher_id)
            if key in completed or self._is_terminal_failure(failures.get(key)):
                continue
            current = self._require_job(job.id)
            if current.cancel_requested:
                self._finish_cancelled(job.id, len(tasks), completed, failures)
                return

            event_type = "metadata.enrichment.completed"
            event_payload: dict[str, Any]
            try:
                enricher = self._enrichers.get(enricher_id)
                if enricher is None or enricher.capabilities.version != expected_version:
                    raise EnricherRequestError(
                        "enricher_version_unavailable",
                        "Frozen metadata enricher version is unavailable",
                        retryable=False,
                    )
                result = await enricher.enrich(self.lookups.get(work_id))
                stored = self._observations.record(result)
                completed[key] = {
                    "observation_id": stored.id,
                    "status": stored.result.status.value,
                    "work_id": stored.result.work_id,
                    "enricher_id": enricher_id,
                }
                failures.pop(key, None)
                event_payload = dict(completed[key])
            except EnricherRequestError as error:
                event_type = "metadata.enrichment.failed"
                failures[key] = {
                    "work_id": work_id,
                    "enricher_id": enricher_id,
                    "code": error.code,
                    "message": str(error),
                    "retryable": error.retryable,
                    "retry_after_seconds": error.retry_after_seconds,
                }
                event_payload = dict(failures[key])
            except Exception:
                event_type = "metadata.enrichment.failed"
                failures[key] = {
                    "work_id": work_id,
                    "enricher_id": enricher_id,
                    "code": "enricher_internal_error",
                    "message": "Metadata enricher failed unexpectedly",
                    "retryable": False,
                    "retry_after_seconds": None,
                }
                event_payload = dict(failures[key])

            progress = self._attempt_progress(len(tasks), completed, failures)
            checkpoint = {"completed": completed, "failures": failures}
            self._jobs.update_state(
                job.id,
                status="running",
                progress=progress,
                checkpoint=checkpoint,
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type=event_type,
                progress=progress,
                payload=event_payload,
            )

        summary = self._summary(len(tasks), completed, failures)
        if not failures:
            status = "succeeded"
            error_code = None
            error_message = None
        elif completed:
            status = "partial_success"
            error_code = "metadata_partial_failure"
            error_message = "Some metadata evidence could not be refreshed"
        else:
            status = "failed"
            error_code = "metadata_enrichment_failed"
            error_message = "Metadata evidence could not be refreshed"
        self._jobs.update_state(
            job.id,
            status=status,
            progress=100,
            result=summary,
            checkpoint={"completed": completed, "failures": failures},
            error_code=error_code,
            error_message=error_message,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type=f"metadata.enrichment.{status}",
            progress=100,
            payload=summary,
        )

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=ENRICHMENT_JOB_KIND)

    def _require_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != ENRICHMENT_JOB_KIND:
            raise ValueError("job is not a metadata enrichment command")
        return job

    @staticmethod
    def _tasks(payload: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
        work_ids = payload.get("work_ids")
        enrichers = payload.get("enrichers")
        if not isinstance(work_ids, list) or not isinstance(enrichers, list):
            raise ValueError("metadata enrichment job payload is invalid")
        tasks: list[tuple[str, str, str]] = []
        for work_id in work_ids:
            if not isinstance(work_id, str):
                raise ValueError("metadata enrichment work ID is invalid")
            for item in enrichers:
                if not isinstance(item, dict):
                    raise ValueError("metadata enricher snapshot is invalid")
                enricher_id = item.get("enricher_id")
                version = item.get("version")
                if not isinstance(enricher_id, str) or not isinstance(version, str):
                    raise ValueError("metadata enricher snapshot is invalid")
                tasks.append((work_id, enricher_id, version))
        return tuple(tasks)

    @staticmethod
    def _read_checkpoint(value: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
        if value is None:
            return {"completed": {}, "failures": {}}
        raw_completed = value.get("completed", {})
        raw_failures = value.get("failures", {})
        if not isinstance(raw_completed, dict) or not isinstance(raw_failures, dict):
            raise ValueError("metadata enrichment checkpoint is invalid")
        return {
            "completed": cast(dict[str, Any], dict(raw_completed)),
            "failures": cast(dict[str, Any], dict(raw_failures)),
        }

    def _finish_cancelled(
        self,
        job_id: str,
        total: int,
        completed: dict[str, Any],
        failures: dict[str, Any],
    ) -> None:
        progress = int((len(completed) * 100) / total) if total else 100
        summary = self._summary(total, completed, failures)
        self._jobs.update_state(
            job_id,
            status="cancelled",
            progress=progress,
            result=summary,
            checkpoint={"completed": completed, "failures": failures},
            error_code="cancelled_by_user",
            error_message="Metadata enrichment was cancelled",
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="metadata.enrichment.cancelled",
            progress=progress,
            payload=summary,
        )

    @staticmethod
    def _summary(
        total: int,
        completed: Mapping[str, Any],
        failures: Mapping[str, Any],
    ) -> dict[str, int]:
        statuses = [
            value.get("status")
            for value in completed.values()
            if isinstance(value, dict)
        ]
        retryable = sum(
            bool(value.get("retryable"))
            for value in failures.values()
            if isinstance(value, dict)
        )
        return {
            "total": total,
            "completed": len(completed),
            "observed": statuses.count("observed"),
            "skipped": len(statuses) - statuses.count("observed"),
            "failed": len(failures),
            "retryable_failed": retryable,
        }

    @staticmethod
    def _is_terminal_failure(value: Any) -> bool:
        return isinstance(value, dict) and value.get("retryable") is False

    @staticmethod
    def _task_key(work_id: str, enricher_id: str) -> str:
        return f"{work_id}|{enricher_id}"

    @staticmethod
    def _attempt_progress(
        total: int,
        completed: Mapping[str, Any],
        failures: Mapping[str, Any],
    ) -> int:
        return int(((len(completed) + len(failures)) * 100) / total) if total else 100

    @classmethod
    def _resume_progress(
        cls,
        total: int,
        completed: Mapping[str, Any],
        failures: Mapping[str, Any],
    ) -> int:
        terminal_failures = sum(cls._is_terminal_failure(value) for value in failures.values())
        return int(((len(completed) + terminal_failures) * 100) / total) if total else 100
