"""Local-session API for creating and downloading portable backups."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from paper_reader.backups.models import BackupArchiveRecord
from paper_reader.backups.service import (
    BackupDownload,
    BackupKind,
    BackupPolicy,
    BackupService,
    BackupUnavailableError,
    RestorePreview,
    RestorePreviewError,
    RestoreService,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/backups",
    dependencies=[Depends(require_local_session)],
    tags=["backups"],
)


class BackupCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[BackupKind.MANUAL] = BackupKind.MANUAL
    encrypted: bool = False
    password: SecretStr | None = Field(default=None, max_length=4096)


class BackupJobResponse(BaseModel):
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


class BackupArchiveResponse(BaseModel):
    id: str
    job_id: str
    kind: BackupKind
    format_version: str
    app_version: str
    schema_revision: str
    byte_size: int
    package_sha256: str
    manifest_sha256: str
    object_count: int
    encrypted: bool
    created_at: datetime


class BackupPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    automatic_interval_days: int
    automatic_retention: int
    check_interval_hours: int
    latest_automatic_at: datetime | None
    next_automatic_at: datetime | None


class RestorePreviewResponse(BaseModel):
    id: str
    validation_job_id: str
    fingerprint: str
    package_sha256: str
    manifest_sha256: str
    encrypted: bool
    source_format_version: str
    source_app_version: str
    source_schema_revision: str
    object_count: int
    payload_bytes: int
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    created_at: datetime
    expires_at: datetime


class RestoreConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=36, max_length=36)
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


def _service(request: Request) -> BackupService:
    return cast(BackupService, request.app.state.backup_service)


def _restore_service(request: Request) -> RestoreService:
    return cast(RestoreService, request.app.state.restore_service)


def _archive_response(record: BackupArchiveRecord) -> BackupArchiveResponse:
    created_at = (
        record.created_at.replace(tzinfo=UTC)
        if record.created_at.tzinfo is None
        else record.created_at
    )
    return BackupArchiveResponse(
        id=record.id,
        job_id=record.job_id,
        kind=BackupKind(record.kind),
        format_version=record.format_version,
        app_version=record.app_version,
        schema_revision=record.schema_revision,
        byte_size=record.byte_size,
        package_sha256=record.package_sha256,
        manifest_sha256=record.manifest_sha256,
        object_count=record.object_count,
        encrypted=record.encrypted,
        created_at=created_at,
    )


def _policy_response(value: BackupPolicy) -> BackupPolicyResponse:
    return BackupPolicyResponse.model_validate(value)


def _preview_response(value: RestorePreview) -> RestorePreviewResponse:
    return RestorePreviewResponse.model_validate(value, from_attributes=True)


def _download_chunks(value: BackupDownload) -> Iterator[bytes]:
    try:
        while chunk := value.stream.read(1024 * 1024):
            yield chunk
    finally:
        value.stream.close()


@router.get("", response_model=tuple[BackupArchiveResponse, ...])
def list_backups(request: Request) -> tuple[BackupArchiveResponse, ...]:
    return tuple(_archive_response(record) for record in _service(request).list_archives())


@router.get("/policy", response_model=BackupPolicyResponse)
def get_backup_policy(request: Request) -> BackupPolicyResponse:
    return _policy_response(_service(request).policy())


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BackupJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def create_backup(
    payload: BackupCommandRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=146),
    ],
) -> BackupJobResponse:
    service = _service(request)
    try:
        job = service.create_job(
            kind=BackupKind.MANUAL,
            idempotency_key=idempotency_key,
            encrypted=payload.encrypted,
            password=(
                payload.password.get_secret_value()
                if payload.password is not None
                else None
            ),
        )
    except ValueError as error:
        response_status = (
            status.HTTP_409_CONFLICT
            if "idempotency" in str(error).casefold()
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=response_status, detail=str(error)) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"backup:{job.id}",
            lambda: service.execute(job.id),
        )
    return BackupJobResponse.model_validate(job)


@router.post(
    "/restore-packages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BackupJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def upload_restore_package(
    request: Request,
    package: Annotated[UploadFile, File()],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=134),
    ],
    password: Annotated[SecretStr | None, Form(max_length=4096)] = None,
) -> BackupJobResponse:
    service = _restore_service(request)
    try:
        upload = await asyncio.to_thread(service.stage_upload, package.file)
        job = service.create_validation_job(
            upload=upload,
            password=(password.get_secret_value() if password is not None else None),
            idempotency_key=idempotency_key,
        )
    except RestorePreviewError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    finally:
        await package.close()
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"restore-validation:{job.id}",
            lambda: service.execute_validation(job.id),
        )
    return BackupJobResponse.model_validate(job)


@router.get(
    "/restore-previews/{preview_id}",
    response_model=RestorePreviewResponse,
)
def get_restore_preview(
    preview_id: str,
    request: Request,
    response: Response,
) -> RestorePreviewResponse:
    try:
        preview = _restore_service(request).get_preview(preview_id)
    except RestorePreviewError as error:
        code = (
            status.HTTP_410_GONE
            if error.code == "preview_expired"
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=code, detail=str(error)) from error
    response.headers["Cache-Control"] = "private, no-store"
    return _preview_response(preview)


@router.post(
    "/restores",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BackupJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
def confirm_restore(
    payload: RestoreConfirmationRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=133),
    ],
) -> BackupJobResponse:
    try:
        job = _restore_service(request).confirm(
            preview_id=payload.preview_id,
            expected_fingerprint=payload.fingerprint,
            idempotency_key=idempotency_key,
        )
    except RestorePreviewError as error:
        unavailable = error.code in {"preview_unavailable", "preview_expired"}
        raise HTTPException(
            status_code=(
                status.HTTP_410_GONE if unavailable else status.HTTP_409_CONFLICT
            ),
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return BackupJobResponse.model_validate(job)


@router.get("/{backup_id}/download", response_class=StreamingResponse)
def download_backup(backup_id: str, request: Request) -> StreamingResponse:
    try:
        download = _service(request).open_download(backup_id)
    except BackupUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This backup package is unavailable or no longer passes integrity checks",
        ) from error
    safe_name = (
        download.filename
        if re.fullmatch(r"[A-Za-z0-9._-]{1,255}", download.filename)
        else "paper-reader-backup.paper-reader-backup"
    )
    return StreamingResponse(
        _download_chunks(download),
        media_type=("application/octet-stream" if download.encrypted else "application/zip"),
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(download.byte_size),
            "X-Content-Type-Options": "nosniff",
            "X-Paper-Reader-SHA256": download.package_sha256,
        },
    )
