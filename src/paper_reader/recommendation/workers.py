"""Background-worker composition for automatic recommendation scoring."""

from __future__ import annotations

from typing import Any, Protocol

from paper_reader.discovery.runs import DiscoveryRun, DiscoveryRunService
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.enrichment_service import MetadataEnrichmentService
from paper_reader.recommendation.orchestration import RecommendationOrchestrator


class RecommendationRunner(Protocol):
    def create_job(
        self,
        run_id: str,
        *,
        trigger: Any,
        trigger_job_id: str | None = None,
    ) -> JobRecord: ...

    async def execute(self, job_id: str) -> JobRecord: ...


async def run_discovery_then_recommend(
    *,
    discovery: DiscoveryRunService,
    recommendations: RecommendationRunner,
    run_id: str,
    retry: bool,
) -> DiscoveryRun:
    """Run discovery and queue scoring only for a usable terminal outcome."""

    run = (
        await discovery.retry_failed(run_id)
        if retry
        else await discovery.execute(run_id)
    )
    if run.status in {"succeeded", "partial_success"}:
        recommendation_job = recommendations.create_job(
            run.id,
            trigger="discovery",
            trigger_job_id=_discovery_trigger_id(run),
        )
        if recommendation_job.status in {"queued", "running"}:
            await recommendations.execute(recommendation_job.id)
    return run


async def run_metadata_then_recommend(
    *,
    metadata: MetadataEnrichmentService,
    jobs: JobRepository,
    recommendations: RecommendationRunner,
    job_id: str,
) -> None:
    """Refresh evidence, then create a distinct immutable scoring pass."""

    await metadata.execute(job_id)
    metadata_job = jobs.get(job_id)
    if metadata_job is None:  # pragma: no cover - service already validated the Job
        raise KeyError(job_id)
    if metadata_job.status not in {"succeeded", "partial_success"}:
        return
    run_id = metadata_job.payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("metadata job has no discovery run")
    recommendation_job = recommendations.create_job(
        run_id,
        trigger="metadata",
        trigger_job_id=metadata_job.id,
    )
    if recommendation_job.status in {"queued", "running"}:
        await recommendations.execute(recommendation_job.id)


async def resume_recommendation(
    service: RecommendationOrchestrator,
    job_id: str,
) -> None:
    await service.execute(job_id)


def _discovery_trigger_id(run: DiscoveryRun) -> str:
    completed_at = run.completed_at.isoformat() if run.completed_at is not None else "pending"
    return ":".join(
        (
            run.job_id,
            completed_at,
            str(run.succeeded_batches),
            str(run.failed_batches),
        )
    )
