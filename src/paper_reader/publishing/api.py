"""Local-session API for privacy previews and explicit Git publication."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.publishing.git import GitPublishingDiagnostic
from paper_reader.publishing.models import PublicationPreview, PublicationSelection
from paper_reader.publishing.privacy import InvalidPublicationSelection
from paper_reader.publishing.repository import StalePublicationPreview
from paper_reader.publishing.service import (
    GitSettingsConfirmation,
    PublishingConflictError,
    PublishingNotReadyError,
    PublishingPreviewEnvelope,
    PublishingService,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/publishing",
    dependencies=[Depends(require_local_session)],
    tags=["publishing"],
)


class GitSettingsConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicationCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: PublicationPreview
    target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublishingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool
    payload: dict[str, Any]
    result: dict[str, Any] | None
    checkpoint: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


def _service(request: Request) -> PublishingService:
    return cast(PublishingService, request.app.state.publishing_service)


@router.get("/git", response_model=GitPublishingDiagnostic)
def git_diagnostic(request: Request, response: Response) -> GitPublishingDiagnostic:
    response.headers["Cache-Control"] = "private, no-store"
    return _service(request).diagnose()


@router.post("/previews", response_model=PublishingPreviewEnvelope)
def preview_publication(
    payload: PublicationSelection,
    request: Request,
    response: Response,
) -> PublishingPreviewEnvelope:
    try:
        value = _service(request).preview(payload)
    except InvalidPublicationSelection as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    response.headers["Cache-Control"] = "private, no-store"
    return value


@router.post(
    "/settings/confirm",
    response_model=GitSettingsConfirmation,
    dependencies=[Depends(require_local_mutation)],
)
def confirm_git_settings(
    payload: GitSettingsConfirmationRequest,
    request: Request,
) -> GitSettingsConfirmation:
    try:
        return _service(request).confirm_settings(payload.target_fingerprint)
    except (PublishingConflictError, PublishingNotReadyError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post(
    "/jobs",
    response_model=PublishingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_local_mutation)],
)
async def create_publication_job(
    payload: PublicationCommandRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=139),
    ],
) -> PublishingJobResponse:
    service = _service(request)
    try:
        job = service.create_job(
            preview=payload.preview,
            target_fingerprint=payload.target_fingerprint,
            idempotency_key=idempotency_key,
        )
    except (PublishingConflictError, PublishingNotReadyError, StalePublicationPreview) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"publishing:{job.id}",
            lambda: service.execute(job.id),
        )
    return PublishingJobResponse.model_validate(job)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=PublishingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_local_mutation)],
)
async def retry_publication_job(
    job_id: str,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=85),
    ],
) -> PublishingJobResponse:
    service = _service(request)
    try:
        job = service.retry_job(job_id, idempotency_key=idempotency_key)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        ) from error
    except (PublishingConflictError, PublishingNotReadyError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"publishing:{job.id}",
            lambda: service.execute(job.id),
        )
    return PublishingJobResponse.model_validate(job)
