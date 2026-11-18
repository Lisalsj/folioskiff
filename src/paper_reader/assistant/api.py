"""Private local API for the unified, evidence-bound paper Assistant."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.agents.evidence import EvidenceConflictError, EvidenceUnavailableError
from paper_reader.assistant.contracts import (
    AssistantMode,
    AssistantOutputLanguage,
    AssistantTaskCommand,
    AssistantTaskStatus,
    ContextScope,
    NetworkPolicy,
    TextSelectionContext,
)
from paper_reader.assistant.repository import (
    AssistantAgentRun,
    AssistantContextSnapshot,
    AssistantFragment,
    AssistantRepositoryError,
    AssistantTask,
)
from paper_reader.assistant.schemas import AssistantCitation
from paper_reader.assistant.service import (
    AssistantExecutionError,
    AssistantNotReadyError,
    AssistantService,
)
from paper_reader.jobs.models import JobRecord
from paper_reader.security import require_local_mutation, require_local_session

_PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
}


def _protect_private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


router = APIRouter(
    prefix="/api/v1/assistant",
    tags=["assistant"],
    dependencies=[
        Depends(require_local_session),
        Depends(_protect_private_response),
    ],
)


class AssistantJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class AssistantContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    work_id: str
    document_version_id: str
    document_content_sha256: str
    scope: ContextScope
    page_numbers: tuple[int, ...]
    selection: TextSelectionContext | None
    annotation_ids: tuple[str, ...]
    section_fallback: bool
    extraction_version: str | None
    warnings: tuple[str, ...]
    evidence_manifest: dict[str, str]
    evidence_sha256: str
    snapshot_sha256: str
    created_at: datetime


class AssistantRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    task_id: str
    attempt: int
    status: AssistantTaskStatus
    backend_id: str
    requested_model_id: str | None
    resolved_model_id: str | None
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    request_sha256: str | None
    prompt_sha256: str | None
    raw_output_sha256: str | None
    usage: dict[str, Any]
    repair_count: int
    error_code: str | None
    diagnostic: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AssistantMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    session_id: str
    task_id: str
    sequence: int
    role: str
    status: str
    content: str
    citations: tuple[AssistantCitation, ...]
    evidence_manifest: dict[str, Any]
    job_id: str
    reply_to_message_id: str | None
    created_at: datetime


class AssistantCheckpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    session_id: str
    source_start_sequence: int
    through_sequence: int
    summary: str
    summary_sha256: str
    backend_id: str
    model_id: str | None
    estimated_tokens: int
    estimated_characters: int
    created_at: datetime


class AssistantTaskSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    retry_of_task_id: str | None
    mode: AssistantMode
    status: AssistantTaskStatus
    network_policy: NetworkPolicy
    backend_id: str
    requested_model_id: str | None
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    output_language: AssistantOutputLanguage
    job_id: str
    error_code: str | None
    diagnostic: str | None
    retryable: bool
    context: AssistantContextResponse
    latest_run: AssistantRunResponse | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class AssistantTaskResponse(AssistantTaskSummaryResponse):
    messages: tuple[AssistantMessageResponse, ...]
    runs: tuple[AssistantRunResponse, ...]


class AssistantWorkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_id: str
    session_id: str | None
    tasks: tuple[AssistantTaskSummaryResponse, ...]
    messages: tuple[AssistantMessageResponse, ...]
    latest_checkpoint: AssistantCheckpointResponse | None


class AssistantFragmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    task_id: str
    sequence: int
    run_id: str
    kind: str
    text: str
    created_at: datetime


def _service(request: Request) -> AssistantService:
    service = cast(AssistantService | None, request.app.state.assistant_service)
    if service is None:  # pragma: no cover - lifespan initializes before requests
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Assistant service is not ready",
        )
    return service


@router.get(
    "/works/{work_id}",
    response_model=AssistantWorkResponse,
)
def get_work_assistant(work_id: str, request: Request) -> AssistantWorkResponse:
    service = _service(request)
    try:
        canonical = service.repository.canonical_work_id(work_id)
        assistant_session = service.repository.find_session_for_work(canonical)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work was not found",
        ) from error
    if assistant_session is None:
        return AssistantWorkResponse(
            work_id=canonical,
            session_id=None,
            tasks=(),
            messages=(),
            latest_checkpoint=None,
        )
    tasks = service.repository.tasks(assistant_session.id)
    messages = service.repository.messages(assistant_session.id)
    checkpoint = service.repository.latest_checkpoint(assistant_session.id)
    return AssistantWorkResponse(
        work_id=canonical,
        session_id=assistant_session.id,
        tasks=tuple(_task_summary(service, item) for item in tasks),
        messages=tuple(AssistantMessageResponse.model_validate(item) for item in messages),
        latest_checkpoint=(
            AssistantCheckpointResponse.model_validate(checkpoint)
            if checkpoint is not None
            else None
        ),
    )


@router.post(
    "/works/{work_id}/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssistantJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def create_assistant_task(
    work_id: str,
    command: AssistantTaskCommand,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> AssistantJobResponse:
    service = _service(request)
    try:
        job = await service.create_task(
            work_id,
            command,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work, document, or Agent backend was not found",
        ) from error
    except EvidenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (
        AssistantExecutionError,
        AssistantNotReadyError,
        AssistantRepositoryError,
        EvidenceConflictError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    _schedule(request, service, job)
    return _job_response(service, job)


@router.get(
    "/tasks/{task_id}",
    response_model=AssistantTaskResponse,
)
def get_assistant_task(task_id: str, request: Request) -> AssistantTaskResponse:
    service = _service(request)
    try:
        task = service.repository.task(task_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant task was not found",
        ) from error
    summary = _task_summary(service, task)
    messages = tuple(
        item for item in service.repository.messages(task.session_id) if item.task_id == task.id
    )
    runs = service.repository.runs(task.id)
    return AssistantTaskResponse(
        **summary.model_dump(),
        messages=tuple(AssistantMessageResponse.model_validate(item) for item in messages),
        runs=tuple(AssistantRunResponse.model_validate(item) for item in runs),
    )


@router.get(
    "/tasks/{task_id}/fragments",
    response_model=tuple[AssistantFragmentResponse, ...],
)
def list_assistant_fragments(
    task_id: str,
    request: Request,
    after_sequence: Annotated[int, Query(ge=-1)] = -1,
) -> tuple[AssistantFragmentResponse, ...]:
    service = _service(request)
    try:
        service.repository.task(task_id)
        fragments: tuple[AssistantFragment, ...] = service.repository.fragments(
            task_id,
            after_sequence=after_sequence,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant task was not found",
        ) from error
    return tuple(AssistantFragmentResponse.model_validate(item) for item in fragments)


@router.post(
    "/tasks/{task_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssistantJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def retry_assistant_task(
    task_id: str,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> AssistantJobResponse:
    service = _service(request)
    try:
        job = await service.retry_task(
            task_id,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant task was not found",
        ) from error
    except (
        AssistantExecutionError,
        AssistantNotReadyError,
        AssistantRepositoryError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    _schedule(request, service, job)
    return _job_response(service, job)


def _schedule(request: Request, service: AssistantService, job: JobRecord) -> None:
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"assistant-reply:{job.id}",
            lambda: service.execute(job.id),
        )


def _job_response(service: AssistantService, job: JobRecord) -> AssistantJobResponse:
    task = service.repository.task_for_job(job.id)
    if task is None:  # pragma: no cover - repository creates both atomically
        raise AssistantExecutionError("Assistant Job has no task")
    return AssistantJobResponse(
        id=job.id,
        task_id=task.id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        cancel_requested=job.cancel_requested,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _task_summary(
    service: AssistantService,
    task: AssistantTask,
) -> AssistantTaskSummaryResponse:
    snapshot = service.repository.snapshot(task.context_snapshot_id)
    runs: tuple[AssistantAgentRun, ...] = service.repository.runs(task.id)
    return AssistantTaskSummaryResponse(
        id=task.id,
        session_id=task.session_id,
        retry_of_task_id=task.retry_of_task_id,
        mode=task.mode,
        status=task.status,
        network_policy=task.network_policy,
        backend_id=task.backend_id,
        requested_model_id=task.requested_model_id,
        reasoning_effort=task.reasoning_effort,
        service_tier=task.service_tier,
        output_language=task.output_language,
        job_id=task.job_id,
        error_code=task.error_code,
        diagnostic=task.diagnostic,
        retryable=task.status in {AssistantTaskStatus.FAILED, AssistantTaskStatus.CANCELLED},
        context=_context_response(snapshot),
        latest_run=(AssistantRunResponse.model_validate(runs[-1]) if runs else None),
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        updated_at=task.updated_at,
    )


def _context_response(snapshot: AssistantContextSnapshot) -> AssistantContextResponse:
    return AssistantContextResponse(
        id=snapshot.id,
        work_id=snapshot.work_id,
        document_version_id=snapshot.document_version_id,
        document_content_sha256=snapshot.document_content_sha256,
        scope=snapshot.scope,
        page_numbers=snapshot.page_numbers,
        selection=(
            TextSelectionContext.model_validate(snapshot.selection)
            if snapshot.selection is not None
            else None
        ),
        annotation_ids=snapshot.annotation_ids,
        section_fallback=snapshot.section_fallback,
        extraction_version=snapshot.extraction_version,
        warnings=snapshot.warnings,
        evidence_manifest={
            "work_id": snapshot.work_id,
            "document_version_id": snapshot.document_version_id,
            "document_content_sha256": snapshot.document_content_sha256,
            "evidence_sha256": snapshot.evidence_sha256,
            "snapshot_sha256": snapshot.snapshot_sha256,
        },
        evidence_sha256=snapshot.evidence_sha256,
        snapshot_sha256=snapshot.snapshot_sha256,
        created_at=snapshot.created_at,
    )


__all__ = ["router"]
