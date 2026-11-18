"""Local settings API for optional model assets."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from paper_reader.assets.repository import AssetInUseError
from paper_reader.assets.service import ModelAssetService
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/settings/model-assets",
    dependencies=[Depends(require_local_session)],
)


class ModelAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    asset_id: str
    capability: Literal["semantic", "ocr"]
    bundle_id: str
    display_name: str
    purpose: str
    version: str
    revision: str
    current_release: bool
    download_size_bytes: int
    license_name: str
    license_url: str
    copyright_notice: str
    source_url: str
    status: str
    integrity: str
    active: bool
    runtime_available: bool
    available: bool
    degradation_reasons: tuple[str, ...]


class AssetJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool
    pause_requested: bool


class AssetJobDetailResponse(AssetJobResponse):
    asset_id: str
    retryable: bool | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class DeletionPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    asset_id: str
    bundle_id: str
    revision: str
    reclaimable_bytes: int
    blocking_references: tuple[str, ...]
    can_delete: bool


def _service(request: Request) -> ModelAssetService:
    service = getattr(request.app.state, "model_asset_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model asset service is unavailable",
        )
    return cast(ModelAssetService, service)


@router.get("", response_model=tuple[ModelAssetResponse, ...], tags=["settings"])
def list_model_assets(request: Request) -> tuple[ModelAssetResponse, ...]:
    return tuple(
        ModelAssetResponse.model_validate(item) for item in _service(request).list_assets()
    )


@router.get(
    "/jobs",
    response_model=tuple[AssetJobDetailResponse, ...],
    tags=["settings", "jobs"],
)
def list_model_asset_jobs(request: Request) -> tuple[AssetJobDetailResponse, ...]:
    responses: list[AssetJobDetailResponse] = []
    for item in _service(request).latest_jobs():
        asset_id = item.payload.get("asset_id")
        if not isinstance(asset_id, str):  # pragma: no cover - filtered by service
            continue
        retryable = item.result.get("retryable") if isinstance(item.result, dict) else None
        responses.append(
            AssetJobDetailResponse(
                id=item.id,
                kind=item.kind,
                status=item.status,
                progress=item.progress,
                cancel_requested=item.cancel_requested,
                pause_requested=item.pause_requested,
                asset_id=asset_id,
                retryable=retryable if isinstance(retryable, bool) else None,
                error_code=item.error_code,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
    return tuple(responses)


@router.post(
    "/{bundle_id}/install",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssetJobResponse,
    tags=["settings", "jobs"],
    dependencies=[Depends(require_local_mutation)],
)
async def install_model_asset(
    bundle_id: str,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)],
) -> AssetJobResponse:
    service = _service(request)
    try:
        job = service.create_install_job(
            bundle_id=bundle_id,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Model bundle not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"model-asset:{job.id}", lambda: service.install(job.id)
        )
    return AssetJobResponse.model_validate(job)


@router.post(
    "/jobs/{job_id}/pause",
    response_model=AssetJobResponse,
    tags=["jobs"],
    dependencies=[Depends(require_local_mutation)],
)
def pause_model_asset_job(job_id: str, request: Request) -> AssetJobResponse:
    return _job_command(_service(request).pause, job_id)


@router.post(
    "/jobs/{job_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssetJobResponse,
    tags=["jobs"],
    dependencies=[Depends(require_local_mutation)],
)
async def resume_model_asset_job(job_id: str, request: Request) -> AssetJobResponse:
    service = _service(request)
    response = _job_command(service.resume, job_id)
    request.app.state.background_tasks.start(
        f"model-asset:{job_id}", lambda: service.install(job_id)
    )
    return response


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=AssetJobResponse,
    tags=["jobs"],
    dependencies=[Depends(require_local_mutation)],
)
def cancel_model_asset_job(job_id: str, request: Request) -> AssetJobResponse:
    return _job_command(_service(request).cancel, job_id)


@router.post(
    "/{bundle_id}/revisions/{revision}/activate",
    response_model=ModelAssetResponse,
    tags=["settings"],
    dependencies=[Depends(require_local_mutation)],
)
async def activate_model_asset(
    bundle_id: str, revision: str, request: Request
) -> ModelAssetResponse:
    try:
        value = await _service(request).activate(bundle_id, revision)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Model revision not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ModelAssetResponse.model_validate(value)


@router.get(
    "/{bundle_id}/revisions/{revision}/deletion-preview",
    response_model=DeletionPreviewResponse,
    tags=["settings"],
)
def preview_model_asset_deletion(
    bundle_id: str, revision: str, request: Request
) -> DeletionPreviewResponse:
    try:
        value = _service(request).deletion_preview(bundle_id, revision)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Model revision not found") from error
    return DeletionPreviewResponse.model_validate(value)


@router.delete(
    "/{bundle_id}/revisions/{revision}",
    response_model=DeletionPreviewResponse,
    tags=["settings"],
    dependencies=[Depends(require_local_mutation)],
)
def delete_model_asset(bundle_id: str, revision: str, request: Request) -> DeletionPreviewResponse:
    try:
        value = _service(request).delete(bundle_id, revision)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Model revision not found") from error
    except AssetInUseError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return DeletionPreviewResponse.model_validate(value)


def _job_command(command: Any, job_id: str) -> AssetJobResponse:
    try:
        job = command(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Model job not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if job is None:
        raise HTTPException(status_code=404, detail="Model job not found")
    return AssetJobResponse.model_validate(job)
