"""Private local API for evidence-bound paper translations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.agents.evidence import EvidenceConflictError, EvidenceUnavailableError
from paper_reader.security import require_local_mutation, require_local_session
from paper_reader.translations.contracts import (
    TranslationArtifact,
    TranslationCommand,
    TranslationEvidenceLevel,
    TranslationLanguage,
    TranslationScope,
    TranslationStatus,
)
from paper_reader.translations.export import StoredTranslationFile, TranslationExporter
from paper_reader.translations.glossary import GlossaryOrigin
from paper_reader.translations.repository import (
    StoredTranslationTerm,
    TranslationRepositoryError,
)
from paper_reader.translations.service import (
    TranslationExecutionError,
    TranslationNotReadyError,
    TranslationService,
    TranslationStart,
)

_PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
}


def _protect_private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


router = APIRouter(
    prefix="/api/v1/translations",
    tags=["translations"],
    dependencies=[Depends(require_local_session), Depends(_protect_private_response)],
)


class TranslationTermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: str
    artifact_id: str
    normalized_source_term: str
    source_term: str
    target_term: str | None
    origin: GlossaryOrigin
    locked: bool
    created_at: datetime
    updated_at: datetime


class TranslationArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    work_id: str
    version: int
    document_version_id: str
    document_content_sha256: str
    context_snapshot_id: str
    task_id: str
    job_id: str
    source_language: TranslationLanguage
    target_language: TranslationLanguage
    scope: TranslationScope
    status: TranslationStatus
    evidence_level: TranslationEvidenceLevel
    prompt_version: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    terms: tuple[TranslationTermResponse, ...]


class TranslationSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    segment_id: str
    segment_index: int
    page_number: int
    block_numbers: tuple[int, ...]
    source_text: str
    source_sha256: str
    translated_text: str | None
    source_kind: str
    ocr_confidence: float | None
    status: str
    diagnostic: str | None
    created_at: datetime
    updated_at: datetime


class TranslationJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    task_id: str
    job_id: str
    kind: str
    status: str
    progress: int
    quick: bool
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class TranslationTermUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_term: str = Field(min_length=1, max_length=1_000)
    locked: bool = True


def _service(request: Request) -> TranslationService:
    service = cast(TranslationService | None, request.app.state.translation_service)
    if service is None:  # pragma: no cover - lifespan initializes before requests
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation service is not ready",
        )
    return service


def _exporter(request: Request) -> TranslationExporter:
    exporter = cast(TranslationExporter | None, request.app.state.translation_exporter)
    if exporter is None:  # pragma: no cover - lifespan initializes before requests
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation exporter is not ready",
        )
    return exporter


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TranslationJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def create_translation(
    command: TranslationCommand,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> TranslationJobResponse:
    service = _service(request)
    try:
        started = await service.create(command, idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation input was not found") from error
    except EvidenceUnavailableError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (
        EvidenceConflictError,
        TranslationExecutionError,
        TranslationNotReadyError,
        TranslationRepositoryError,
        ValueError,
    ) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _schedule(request, service, started)
    return _job_response(service, started)


@router.get("/{artifact_id}", response_model=TranslationArtifactResponse)
def get_translation(artifact_id: str, request: Request) -> TranslationArtifactResponse:
    service = _service(request)
    try:
        artifact = service.repository.artifact(artifact_id)
        terms = service.repository.term_records(artifact.id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation was not found") from error
    return _artifact_response(artifact, terms)


@router.get(
    "/{artifact_id}/segments",
    response_model=tuple[TranslationSegmentResponse, ...],
)
def list_translation_segments(
    artifact_id: str,
    request: Request,
) -> tuple[TranslationSegmentResponse, ...]:
    try:
        segments = _service(request).repository.segments(artifact_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation was not found") from error
    return tuple(TranslationSegmentResponse.model_validate(item) for item in segments)


@router.put(
    "/{artifact_id}/terms/{term_id}",
    response_model=TranslationTermResponse,
    dependencies=[Depends(require_local_mutation)],
)
def update_translation_term(
    artifact_id: str,
    term_id: str,
    update: TranslationTermUpdateRequest,
    request: Request,
    _idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> TranslationTermResponse:
    try:
        term = _service(request).repository.set_term_by_id(
            artifact_id,
            term_id,
            target_term=update.target_term,
            locked=update.locked,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation term was not found") from error
    except (TranslationRepositoryError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return TranslationTermResponse.model_validate(term)


@router.post(
    "/{artifact_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TranslationJobResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def resume_translation(
    artifact_id: str,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> TranslationJobResponse:
    service = _service(request)
    try:
        started = service.resume(artifact_id, idempotency_key=idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation was not found") from error
    except (TranslationRepositoryError, TranslationExecutionError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _schedule(request, service, started)
    return _job_response(service, started)


@router.get("/{artifact_id}/export.md")
def export_translation_markdown(
    artifact_id: str,
    request: Request,
    draft: Annotated[bool, Query()] = False,
) -> Response:
    return _export_response(request, artifact_id, format="markdown", draft=draft)


@router.get("/{artifact_id}/export.html")
def export_translation_html(
    artifact_id: str,
    request: Request,
    draft: Annotated[bool, Query()] = False,
) -> Response:
    return _export_response(request, artifact_id, format="html", draft=draft)


@router.get("/{artifact_id}/print")
def print_translation(
    artifact_id: str,
    request: Request,
    draft: Annotated[bool, Query()] = False,
) -> Response:
    try:
        body = _exporter(request).print_html(artifact_id, draft=draft)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation was not found") from error
    except (TranslationRepositoryError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(
        content=body,
        media_type="text/html",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "font-src 'none'; frame-src 'none'"
            ),
        },
    )


def _schedule(
    request: Request,
    service: TranslationService,
    started: TranslationStart,
) -> None:
    if started.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"translation:{started.job_id}",
            lambda: service.execute(started.job_id),
        )


def _job_response(
    service: TranslationService,
    started: TranslationStart,
) -> TranslationJobResponse:
    job = service.jobs.get(started.job_id)
    if job is None:  # pragma: no cover - repository creates both atomically
        raise TranslationExecutionError("Translation Job disappeared")
    return TranslationJobResponse(
        artifact_id=started.artifact_id,
        task_id=started.task_id,
        job_id=job.id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        quick=started.quick,
        cancel_requested=job.cancel_requested,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _artifact_response(
    artifact: TranslationArtifact,
    terms: tuple[StoredTranslationTerm, ...],
) -> TranslationArtifactResponse:
    return TranslationArtifactResponse(
        **artifact.model_dump(),
        terms=tuple(TranslationTermResponse.model_validate(item) for item in terms),
    )


def _export_response(
    request: Request,
    artifact_id: str,
    *,
    format: str,
    draft: bool,
) -> Response:
    try:
        stored = _exporter(request).store(artifact_id, format=format, draft=draft)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Translation was not found") from error
    except (TranslationRepositoryError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    headers = {
        **_PRIVATE_HEADERS,
        "Content-Disposition": _content_disposition(stored),
    }
    if format == "html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
            "font-src 'none'; frame-src 'none'"
        )
    return Response(
        content=stored.path.read_bytes(),
        media_type=stored.media_type,
        headers=headers,
    )


def _content_disposition(stored: StoredTranslationFile) -> str:
    extension = "md" if stored.format == "markdown" else "html"
    suffix = "-draft" if stored.draft else ""
    fallback = f"paper-reader-translation{suffix}.{extension}"
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(stored.filename, safe='.-_')}"
    )


__all__ = ["router"]
