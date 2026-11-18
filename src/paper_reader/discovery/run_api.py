"""HTTP 202 commands for source preview and durable discovery runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.discovery.identity import IdentityRepository
from paper_reader.discovery.keywords import (
    KeywordRepository,
    KeywordVersionNotFoundError,
    UnconfirmedKeywordVersionError,
)
from paper_reader.discovery.plans import (
    DiscoveryPlan,
    DiscoveryPlanCreate,
    DiscoveryPlanRepository,
    PlanScheduler,
    freeze_default_timezone,
)
from paper_reader.discovery.preview import DiscoveryPreview, DiscoveryPreviewService
from paper_reader.discovery.runs import (
    DiscoveryRun,
    DiscoveryRunRepository,
    DiscoveryRunService,
)
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.orchestration import RecommendationOrchestrator
from paper_reader.recommendation.workers import run_discovery_then_recommend
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1",
    tags=["runs"],
    dependencies=[Depends(require_local_session)],
)

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", max_length=120),
]


class AcceptedJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool


class PreviewPaperResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    connector_id: str
    external_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    doi: str | None
    published_at: datetime | None
    updated_at: datetime | None
    landing_url: str | None
    field_provenance: dict[str, str]
    raw_payload: dict[str, Any]


class SourcePreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    connector_id: str
    display_name: str
    status: str
    estimate: int | None
    estimate_exact: bool
    samples: tuple[PreviewPaperResponse, ...]
    keyword_impact: dict[str, int] | None
    warnings: tuple[str, ...]
    error_code: str | None
    error_message: str | None
    retryable: bool
    setup_url: str | None


class DiscoveryPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: str
    keyword_version_id: str
    months: tuple[str, ...]
    warnings: tuple[str, ...]
    sources: tuple[SourcePreviewResponse, ...]


class SourceFilterCountsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evaluated_count: int = Field(ge=0)
    missing_updated_at_count: int = Field(ge=0)
    outside_date_range_count: int = Field(ge=0)
    in_date_range_count: int = Field(ge=0)
    keyword_mismatch_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)


class RunBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    month: str
    connector_id: str
    status: str
    query_fingerprint: str
    checkpoint: dict[str, Any]
    filter_counts: SourceFilterCountsResponse | None = None
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
    completed_at: datetime | None


class DiscoveryRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_id: str
    job_id: str
    keyword_version_id: str
    status: str
    months: tuple[str, ...]
    connector_versions: dict[str, str]
    total_batches: int
    succeeded_batches: int
    failed_batches: int
    cancelled_batches: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    batches: tuple[RunBatchResponse, ...]

    @classmethod
    def from_domain(cls, run: DiscoveryRun) -> DiscoveryRunResponse:
        return cls.model_validate(run)


class DiscoveryRunPageResponse(BaseModel):
    items: tuple[DiscoveryRunResponse, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class QueuedDiscoveryRun:
    service: DiscoveryRunService
    run: DiscoveryRun
    job: JobRecord


def _run_service(request: Request) -> DiscoveryRunService:
    sessions = request.app.state.sessions
    return DiscoveryRunService(
        plans=DiscoveryPlanRepository(sessions),
        keywords=KeywordRepository(sessions),
        connectors=request.app.state.connector_registry,
        identities=IdentityRepository(
            sessions,
            work_index=request.app.state.lexical_index,
        ),
        jobs=JobRepository(sessions),
        runs=DiscoveryRunRepository(sessions),
    )


def _accepted(job: JobRecord) -> AcceptedJobResponse:
    return AcceptedJobResponse.model_validate(job)


def queue_discovery_run(
    request: Request,
    *,
    plan_id: str,
    idempotency_key: str | None,
) -> QueuedDiscoveryRun:
    service = _run_service(request)
    run = service.create(plan_id, idempotency_key=idempotency_key)
    job = JobRepository(request.app.state.sessions).get(run.job_id)
    if job is None:  # pragma: no cover - protected by foreign keys
        raise RuntimeError("Discovery run points to a missing Job")
    return QueuedDiscoveryRun(service=service, run=run, job=job)


def launch_discovery_run(request: Request, queued: QueuedDiscoveryRun) -> None:
    if queued.job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"run:{queued.run.id}",
            partial(
                _execute_run,
                queued.service,
                request.app.state.recommendation_orchestrator,
                queued.run.id,
                retry=False,
            ),
        )


async def _execute_run(
    service: DiscoveryRunService,
    recommendations: RecommendationOrchestrator,
    run_id: str,
    *,
    retry: bool,
) -> None:
    await run_discovery_then_recommend(
        discovery=service,
        recommendations=recommendations,
        run_id=run_id,
        retry=retry,
    )


async def _execute_preview(
    request: Request,
    *,
    job_id: str,
    plan_id: str,
    draft_plan: DiscoveryPlan | None = None,
) -> None:
    sessions = request.app.state.sessions
    jobs = JobRepository(sessions)
    jobs.update_state(job_id, status="running", progress=0)
    jobs.append_event(
        job_id=job_id,
        event_type="discovery.preview.started",
        progress=0,
        payload={"plan_id": plan_id},
    )
    service = DiscoveryPreviewService(
        plans=DiscoveryPlanRepository(sessions),
        keywords=KeywordRepository(sessions),
        connectors=request.app.state.connector_registry,
    )
    try:
        result: DiscoveryPreview = (
            await service.preview_plan(draft_plan)
            if draft_plan is not None
            else await service.preview(plan_id)
        )
        serialized = DiscoveryPreviewResponse.model_validate(result).model_dump(mode="json")
    except Exception as error:
        jobs.update_state(
            job_id,
            status="failed",
            progress=100,
            error_code="preview_internal_error",
            error_message=type(error).__name__,
        )
        jobs.append_event(
            job_id=job_id,
            event_type="discovery.preview.failed",
            progress=100,
            payload={"plan_id": plan_id},
        )
        return
    jobs.update_state(
        job_id,
        status="succeeded",
        progress=100,
        result=serialized,
    )
    jobs.append_event(
        job_id=job_id,
        event_type="discovery.preview.succeeded",
        progress=100,
        payload={"plan_id": plan_id},
    )


def _draft_plan(payload: DiscoveryPlanCreate) -> DiscoveryPlan:
    serialized = payload.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(
            serialized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return DiscoveryPlan(
        id=f"draft:{fingerprint[:24]}",
        name=payload.name,
        research_intent=payload.research_intent,
        keyword_version_id=payload.keyword_version_id,
        date_range=payload.date_range,
        sources=payload.sources,
        source_filters=payload.source_filters,
        schedule=payload.schedule,
        quota=payload.quota,
        enabled=payload.enabled,
        created_at=datetime.now(UTC),
    )


@router.post(
    "/plans/preview",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def start_draft_preview(
    payload: DiscoveryPlanCreate,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> AcceptedJobResponse:
    payload = freeze_default_timezone(
        payload,
        resolved_timezone=request.app.state.user_preferences_service.resolved_timezone(),
    )
    keywords = KeywordRepository(request.app.state.sessions)
    try:
        keywords.compile(payload.keyword_version_id)
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword version not found",
        ) from error
    except UnconfirmedKeywordVersionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    plan = _draft_plan(payload)
    jobs = JobRepository(request.app.state.sessions)
    job = jobs.create(
        kind="discovery.preview",
        payload={"draft_plan": payload.model_dump(mode="json")},
        idempotency_key=(
            f"discovery.preview:{idempotency_key}" if idempotency_key is not None else None
        ),
    )
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"preview:{job.id}",
            partial(
                _execute_preview,
                request,
                job_id=job.id,
                plan_id=plan.id,
                draft_plan=plan,
            ),
        )
    return _accepted(job)


@router.post(
    "/plans/{plan_id}/preview",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def start_preview(
    plan_id: str,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> AcceptedJobResponse:
    plans = DiscoveryPlanRepository(request.app.state.sessions)
    try:
        plans.get(plan_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        ) from error
    jobs = JobRepository(request.app.state.sessions)
    job = jobs.create(
        kind="discovery.preview",
        payload={"plan_id": plan_id},
        idempotency_key=(
            f"discovery.preview:{idempotency_key}" if idempotency_key is not None else None
        ),
    )
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"preview:{job.id}",
            partial(_execute_preview, request, job_id=job.id, plan_id=plan_id),
        )
    return _accepted(job)


@router.get(
    "/plans/{plan_id}/runs",
    response_model=DiscoveryRunPageResponse,
)
def list_plan_runs(
    plan_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiscoveryRunPageResponse:
    try:
        DiscoveryPlanRepository(request.app.state.sessions).get(plan_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        ) from error
    page = DiscoveryRunRepository(request.app.state.sessions).list_for_plan(
        plan_id,
        limit=limit,
        offset=offset,
    )
    return DiscoveryRunPageResponse(
        items=tuple(DiscoveryRunResponse.from_domain(run) for run in page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/plans/{plan_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def start_run(
    plan_id: str,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> AcceptedJobResponse:
    try:
        queued = queue_discovery_run(
            request,
            plan_id=plan_id,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        ) from error
    launch_discovery_run(request, queued)
    return _accepted(queued.job)


@router.post(
    "/plans/catchups/{catchup_id}/confirm",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def confirm_catchup(catchup_id: str, request: Request) -> AcceptedJobResponse:
    scheduler = PlanScheduler(request.app.state.sessions)
    try:
        catchup = scheduler.get(catchup_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catch-up item not found",
        ) from error
    try:
        queued = queue_discovery_run(
            request,
            plan_id=catchup.plan_id,
            idempotency_key=f"catchup:{catchup.id}",
        )
    except KeyError as error:  # pragma: no cover - guarded by the catch-up foreign key
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Catch-up plan no longer exists",
        ) from error
    scheduler.confirm(catchup.id)
    launch_discovery_run(request, queued)
    return _accepted(queued.job)


@router.get("/runs/{run_id}", response_model=DiscoveryRunResponse)
def get_run(run_id: str, request: Request) -> DiscoveryRunResponse:
    try:
        run = DiscoveryRunRepository(request.app.state.sessions).get(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        ) from error
    return DiscoveryRunResponse.from_domain(run)


@router.post(
    "/runs/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def retry_run(run_id: str, request: Request) -> AcceptedJobResponse:
    repository = DiscoveryRunRepository(request.app.state.sessions)
    try:
        run = repository.get(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        ) from error
    if not any(batch.status == "failed" and batch.retryable for batch in run.batches):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run has no retryable failed batch",
        )
    jobs = JobRepository(request.app.state.sessions)
    job = jobs.get(run.job_id)
    if job is None:  # pragma: no cover - protected by foreign keys
        raise RuntimeError("Discovery run points to a missing Job")
    progress = int((run.succeeded_batches * 100) / run.total_batches)
    job = jobs.update_state(
        job.id,
        status="queued",
        progress=progress,
        checkpoint=job.checkpoint,
    )
    service = _run_service(request)
    request.app.state.background_tasks.start(
        f"run:{run.id}",
        partial(
            _execute_run,
            service,
            request.app.state.recommendation_orchestrator,
            run.id,
            retry=True,
        ),
    )
    return _accepted(job)
