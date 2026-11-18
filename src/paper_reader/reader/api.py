"""HTTP API for local library imports and paper read models."""

from __future__ import annotations

import json
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
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.reader.acquisition import (
    AcquisitionReason,
    AcquisitionRequest,
    DocumentAcquisitionService,
)
from paper_reader.reader.annotations import (
    Annotation,
    AnnotationColor,
    AnnotationConflictError,
    AnnotationInput,
    AnnotationKind,
    AnnotationNotFoundError,
    AnnotationRepository,
    PdfRect,
    ReadingPosition,
    ReadingPositionInput,
)
from paper_reader.reader.content import (
    DocumentContentNotFoundError,
    DocumentContentService,
    DocumentContentUnavailableError,
)
from paper_reader.reader.extraction import (
    DocumentExtractionService,
    PageEvidence,
)
from paper_reader.reader.imports import (
    LibraryImportService,
    PdfImportError,
    PdfImportInput,
    PdfImportResult,
    ReferenceImportError,
    ReferenceImportInput,
    ReferenceImportResult,
    ReferenceKind,
    WorkNotFoundError,
)
from paper_reader.reader.migrations import (
    AnnotationMigrationService,
    MigrationCancelledError,
    MigrationCandidate,
    MigrationConfidence,
    MigrationConflictError,
    MigrationDecision,
    MigrationMatchState,
    MigrationNotFoundError,
    MigrationPreview,
    TextSource,
)
from paper_reader.reader.ocr_contracts import OcrCapability, OcrError
from paper_reader.reader.ocr_service import OcrService
from paper_reader.reader.repository import (
    LibraryDocument,
    LibraryPage,
    LibraryRepository,
    LibraryWork,
)
from paper_reader.reader.retention import (
    CleanupCandidate,
    CleanupPreview,
    CleanupPreviewError,
    CleanupResult,
    RetentionService,
)
from paper_reader.reader.storage import (
    ContentStore,
    DocumentTooLargeError,
    InvalidPdfError,
    StorageCancelledError,
    UnsafeStoredPathError,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_local_session)],
)


class ReferenceImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ReferenceKind
    value: str = Field(min_length=1, max_length=100_000)
    title: str | None = Field(default=None, max_length=2_000)
    authors: tuple[str, ...] = Field(default=(), max_length=200)
    rights_confirmed: bool = False


class ReferenceImportResponse(BaseModel):
    work_id: str
    created_work: bool
    kind: ReferenceKind
    normalized_value: str


class PdfImportResponse(BaseModel):
    work_id: str
    document_version_id: str
    content_sha256: str
    created_work: bool
    created_document: bool


class AcquisitionCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: str | None = Field(default=None, max_length=36)
    url: str = Field(min_length=1, max_length=2_048)
    access_status: Literal["open"]
    reason: AcquisitionReason


class AcquisitionJobResponse(BaseModel):
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


class ExtractionJobResponse(AcquisitionJobResponse):
    pass


class TextBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_number: int
    confidence: float | None


class PageEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_version_id: str
    page_number: int
    page_width: float
    page_height: float
    text: str
    text_source: Literal["native", "ocr"]
    blocks: tuple[TextBlockResponse, ...]
    native_text: str
    native_blocks: tuple[TextBlockResponse, ...]
    native_character_count: int
    likely_scanned: bool
    ocr_state: Literal[
        "not_needed",
        "pending",
        "complete",
        "failed",
        "unavailable",
    ]
    ocr_text: str | None
    ocr_blocks: tuple[TextBlockResponse, ...]
    ocr_confidence: float | None
    ocr_engine_version: str | None
    ocr_model_bundle_id: str | None
    extractor_version: str


class OcrModelFileStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: Literal["det", "cls", "rec"]
    filename: str
    size: int
    sha256: str
    installed: bool
    integrity: Literal["missing", "verified", "hash_mismatch"]


class OcrCapabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    available: bool
    runtime_available: bool
    models_installed: bool
    bundle_id: str
    display_name: str
    version: str
    total_size: int
    license_name: str
    license_url: str
    copyright_notice: str
    source_url: str
    source_revision: str
    files: tuple[OcrModelFileStatusResponse, ...]
    degradation_reasons: tuple[str, ...]


class OcrJobResponse(AcquisitionJobResponse):
    pass


class CleanupPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(default=90, ge=1, le=3650)


class CleanupApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str = Field(pattern=r"^[0-9a-f]{64}$")


class CleanupCandidateResponse(BaseModel):
    content_sha256: str
    byte_size: int
    work_ids: tuple[str, ...]
    age_days: int
    protected_reasons: tuple[str, ...]
    eligible: bool


class CleanupPreviewResponse(BaseModel):
    preview_token: str
    generated_at: datetime
    retention_days: int
    candidates: tuple[CleanupCandidateResponse, ...]
    eligible_count: int
    protected_count: int
    reclaimable_bytes: int


class CleanupResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preview_token: str
    deleted_objects: int
    missing_objects: int
    cleared_document_versions: int
    reclaimed_bytes: int
    cancelled: bool


class LibraryDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    work_id: str
    content_sha256: str
    access_status: str
    media_type: str
    completeness: int
    extraction_status: str
    available: bool
    byte_size: int
    original_filename: str | None
    acquisition_kind: str
    last_accessed_at: datetime | None
    created_at: datetime
    is_representative: bool


class LibraryWorkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    doi: str | None
    published_at: datetime | None
    added_via: str
    added_at: datetime
    favorite: bool
    feedback: str
    pinned: bool
    reading_progress: float
    analysis_state: str
    document_count: int
    representative_document: LibraryDocumentResponse | None


class LibraryPageResponse(BaseModel):
    items: tuple[LibraryWorkResponse, ...]
    total: int
    limit: int
    offset: int


class ReadingPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    document_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1, le=1_000_000)
    page_count: int = Field(ge=1, le=1_000_000)
    scale: float = Field(ge=0.5, le=3)
    rotation: Literal[0, 90, 180, 270]


class ReadingPositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_version_id: str
    document_content_sha256: str
    page_number: int
    page_count: int
    scale: float
    rotation: Literal[0, 90, 180, 270]
    progress: float
    created_at: datetime
    updated_at: datetime


class PdfRectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x0: float = Field(ge=0, le=100_000)
    y0: float = Field(ge=0, le=100_000)
    x1: float = Field(gt=0, le=100_000)
    y1: float = Field(gt=0, le=100_000)


class PdfRectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    x0: float
    y0: float
    x1: float
    y1: float


class AnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    document_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: AnnotationKind
    page_number: int = Field(ge=1, le=1_000_000)
    page_count: int = Field(ge=1, le=1_000_000)
    page_width: float = Field(gt=0, le=100_000)
    page_height: float = Field(gt=0, le=100_000)
    quote_text: str | None = Field(default=None, max_length=10_000)
    context_before: str | None = Field(default=None, max_length=2_000)
    context_after: str | None = Field(default=None, max_length=2_000)
    rects: tuple[PdfRectRequest, ...] = Field(default=(), max_length=64)
    color: AnnotationColor
    note: str | None = Field(default=None, max_length=20_000)


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_version_id: str
    document_content_sha256: str
    kind: AnnotationKind
    page_number: int
    page_width: float
    page_height: float
    quote_text: str | None
    context_before: str | None
    context_after: str | None
    rects: tuple[PdfRectResponse, ...]
    color: AnnotationColor
    note: str | None
    created_at: datetime
    updated_at: datetime


class AnnotationMigrationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_document_version_id: str = Field(min_length=1, max_length=100)


class AnnotationMigrationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: MigrationDecision


class MigrationAlternativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    page_width: float
    page_height: float
    quote_text: str
    rects: tuple[PdfRectResponse, ...]
    text_source: TextSource
    score: float


class MigrationCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    batch_id: str
    source_annotation_id: str
    source_kind: AnnotationKind
    source_page_number: int
    source_quote_text: str | None
    source_note: str | None
    source_color: AnnotationColor
    match_state: MigrationMatchState
    confidence: MigrationConfidence
    score: float
    target_page_number: int | None
    target_page_width: float | None
    target_page_height: float | None
    target_quote_text: str | None
    target_context_before: str | None
    target_context_after: str | None
    target_rects: tuple[PdfRectResponse, ...]
    text_source: TextSource | None
    alternatives: tuple[MigrationAlternativeResponse, ...]
    decision: MigrationDecision | None
    target_annotation_id: str | None
    created_at: datetime
    decided_at: datetime | None


class MigrationCoverageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    matched: int
    ambiguous: int
    orphaned: int
    pending: int
    accepted: int
    rejected: int


class MigrationPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    algorithm_version: str
    source_document_version_id: str
    source_document_content_sha256: str
    target_document_version_id: str
    target_document_content_sha256: str
    target_extraction_sha256: str
    candidates: tuple[MigrationCandidateResponse, ...]
    coverage: MigrationCoverageResponse
    created_at: datetime


def _imports(request: Request) -> LibraryImportService:
    return LibraryImportService(
        sessions=request.app.state.sessions,
        store=ContentStore(request.app.state.paths.library_root),
        work_index=request.app.state.lexical_index,
    )


def _library(request: Request) -> LibraryRepository:
    return LibraryRepository(request.app.state.sessions)


def _content(request: Request) -> DocumentContentService:
    return DocumentContentService(
        sessions=request.app.state.sessions,
        store=ContentStore(request.app.state.paths.library_root),
    )


def _annotations(request: Request) -> AnnotationRepository:
    return AnnotationRepository(request.app.state.sessions)


def _annotation_migrations(request: Request) -> AnnotationMigrationService:
    return AnnotationMigrationService(request.app.state.sessions)


def _acquisition(request: Request) -> DocumentAcquisitionService:
    return cast(
        DocumentAcquisitionService,
        request.app.state.document_acquisition_service,
    )


def _extraction(request: Request) -> DocumentExtractionService:
    return cast(
        DocumentExtractionService,
        request.app.state.document_extraction_service,
    )


def _ocr(request: Request) -> OcrService:
    return cast(OcrService, request.app.state.ocr_service)


def _retention(request: Request) -> RetentionService:
    return cast(RetentionService, request.app.state.retention_service)


def _reference_response(value: ReferenceImportResult) -> ReferenceImportResponse:
    return ReferenceImportResponse(
        work_id=value.work_id,
        created_work=value.created_work,
        kind=value.kind,
        normalized_value=value.normalized_value,
    )


def _pdf_response(value: PdfImportResult) -> PdfImportResponse:
    return PdfImportResponse(
        work_id=value.work_id,
        document_version_id=value.document_version_id,
        content_sha256=value.content_sha256,
        created_work=value.created_work,
        created_document=value.created_document,
    )


def _cleanup_candidate_response(
    value: CleanupCandidate,
) -> CleanupCandidateResponse:
    return CleanupCandidateResponse(
        content_sha256=value.content_sha256,
        byte_size=value.byte_size,
        work_ids=value.work_ids,
        age_days=value.age_days,
        protected_reasons=value.protected_reasons,
        eligible=value.eligible,
    )


def _cleanup_preview_response(value: CleanupPreview) -> CleanupPreviewResponse:
    return CleanupPreviewResponse(
        preview_token=value.preview_token,
        generated_at=value.generated_at,
        retention_days=value.retention_days,
        candidates=tuple(_cleanup_candidate_response(candidate) for candidate in value.candidates),
        eligible_count=value.eligible_count,
        protected_count=value.protected_count,
        reclaimable_bytes=value.reclaimable_bytes,
    )


def _page_evidence_response(value: PageEvidence) -> PageEvidenceResponse:
    return PageEvidenceResponse.model_validate(value)


def _ocr_capability_response(value: OcrCapability) -> OcrCapabilityResponse:
    return OcrCapabilityResponse.model_validate(value)


def _ocr_conflict(error: OcrError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
        },
    )


def _document_response(value: LibraryDocument) -> LibraryDocumentResponse:
    return LibraryDocumentResponse.model_validate(value)


def _work_response(value: LibraryWork) -> LibraryWorkResponse:
    return LibraryWorkResponse.model_validate(value)


def _page_response(value: LibraryPage) -> LibraryPageResponse:
    return LibraryPageResponse(
        items=tuple(_work_response(item) for item in value.items),
        total=value.total,
        limit=value.limit,
        offset=value.offset,
    )


def _position_input(value: ReadingPositionRequest) -> ReadingPositionInput:
    return ReadingPositionInput(
        document_content_sha256=value.document_content_sha256,
        page_number=value.page_number,
        page_count=value.page_count,
        scale=value.scale,
        rotation=value.rotation,
    )


def _annotation_input(value: AnnotationRequest) -> AnnotationInput:
    return AnnotationInput(
        document_content_sha256=value.document_content_sha256,
        kind=value.kind,
        page_number=value.page_number,
        page_count=value.page_count,
        page_width=value.page_width,
        page_height=value.page_height,
        quote_text=value.quote_text,
        context_before=value.context_before,
        context_after=value.context_after,
        rects=tuple(
            PdfRect(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1) for rect in value.rects
        ),
        color=value.color,
        note=value.note,
    )


def _position_response(value: ReadingPosition) -> ReadingPositionResponse:
    return ReadingPositionResponse.model_validate(value)


def _annotation_response(value: Annotation) -> AnnotationResponse:
    return AnnotationResponse.model_validate(value)


def _migration_candidate_response(
    value: MigrationCandidate,
) -> MigrationCandidateResponse:
    return MigrationCandidateResponse.model_validate(value)


def _migration_preview_response(value: MigrationPreview) -> MigrationPreviewResponse:
    return MigrationPreviewResponse.model_validate(value)


def _annotation_http_error(error: Exception) -> HTTPException:
    if isinstance(error, AnnotationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document or annotation was not found in the local library",
        )
    if isinstance(error, AnnotationConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(error),
    )


def _migration_http_error(error: Exception) -> HTTPException:
    if isinstance(error, MigrationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Annotation migration resource was not found in the local library",
        )
    if isinstance(error, (MigrationConflictError, MigrationCancelledError)):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(error),
    )


def _upload_chunks(upload: UploadFile) -> Iterator[bytes]:
    while chunk := upload.file.read(64 * 1024):
        yield chunk


def _parse_authors(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise PdfImportError("authors must be a JSON array of strings") from error
    if (
        not isinstance(parsed, list)
        or len(parsed) > 200
        or any(not isinstance(author, str) for author in parsed)
    ):
        raise PdfImportError("authors must be a JSON array of at most 200 strings")
    return tuple(parsed)


def _not_found(work_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Work not found in local library: {work_id}",
    )


@router.post(
    "/library/imports/reference",
    status_code=status.HTTP_201_CREATED,
    response_model=ReferenceImportResponse,
    tags=["library"],
    dependencies=[Depends(require_local_mutation)],
)
def import_reference(
    payload: ReferenceImportRequest,
    request: Request,
) -> ReferenceImportResponse:
    try:
        result = _imports(request).import_reference(
            ReferenceImportInput(
                kind=payload.kind,
                value=payload.value,
                title=payload.title,
                authors=payload.authors,
                rights_confirmed=payload.rights_confirmed,
            )
        )
    except ReferenceImportError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _reference_response(result)


@router.post(
    "/library/imports/pdf",
    status_code=status.HTTP_201_CREATED,
    response_model=PdfImportResponse,
    tags=["library"],
    dependencies=[Depends(require_local_mutation)],
)
async def import_pdf(
    request: Request,
    file: Annotated[UploadFile, File()],
    work_id: Annotated[str | None, Form(max_length=36)] = None,
    title: Annotated[str | None, Form(max_length=2_000)] = None,
    authors: Annotated[str, Form(max_length=100_000)] = "[]",
    doi: Annotated[str | None, Form(max_length=255)] = None,
    rights_confirmed: Annotated[bool, Form()] = False,
) -> PdfImportResponse:
    try:
        content_type = (file.content_type or "application/pdf").split(";", 1)[0]
        if content_type.casefold() != "application/pdf":
            raise InvalidPdfError("Uploaded content type must be application/pdf")
        result = _imports(request).import_pdf(
            PdfImportInput(
                work_id=work_id,
                title=title,
                authors=_parse_authors(authors),
                doi=doi,
                original_filename=file.filename or "paper.pdf",
                rights_confirmed=rights_confirmed,
            ),
            _upload_chunks(file),
        )
    except WorkNotFoundError as error:
        raise _not_found(work_id or "unknown") from error
    except DocumentTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error
    except (PdfImportError, InvalidPdfError, StorageCancelledError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    finally:
        await file.close()
    return _pdf_response(result)


@router.post(
    "/library/works/{work_id}/documents/acquire",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcquisitionJobResponse,
    tags=["library", "jobs"],
    dependencies=[Depends(require_local_mutation)],
)
async def acquire_open_document(
    work_id: str,
    payload: AcquisitionCommandRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> AcquisitionJobResponse:
    service = _acquisition(request)
    try:
        job = service.create_job(
            AcquisitionRequest(
                work_id=work_id,
                source_record_id=payload.source_record_id,
                url=payload.url,
                access_status=payload.access_status,
                reason=payload.reason,
            ),
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise _not_found(work_id) from error
    except ReferenceImportError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except ValueError as error:
        response_status = (
            status.HTTP_409_CONFLICT
            if "idempotency" in str(error).casefold()
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=response_status, detail=str(error)) from error

    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"reader-acquisition:{job.id}",
            lambda: service.execute(job.id),
        )
    return AcquisitionJobResponse.model_validate(job)


@router.post(
    "/library/works/{work_id}/documents/{document_version_id}/extract",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExtractionJobResponse,
    tags=["library", "jobs"],
    dependencies=[Depends(require_local_mutation)],
)
async def extract_document_pages(
    work_id: str,
    document_version_id: str,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> ExtractionJobResponse:
    service = _extraction(request)
    try:
        job = service.create_job(
            work_id=work_id,
            document_version_id=document_version_id,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work or document was not found in the local library",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"reader-extraction:{job.id}",
            lambda: service.execute(job.id),
        )
    return ExtractionJobResponse.model_validate(job)


@router.get(
    "/works/{work_id}/documents/{document_version_id}/pages",
    response_model=tuple[PageEvidenceResponse, ...],
    tags=["works", "library"],
)
def list_document_pages(
    work_id: str,
    document_version_id: str,
    request: Request,
) -> tuple[PageEvidenceResponse, ...]:
    try:
        pages = _extraction(request).list_pages(
            work_id=work_id,
            document_version_id=document_version_id,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work or document was not found in the local library",
        ) from error
    return tuple(_page_evidence_response(page) for page in pages)


@router.get(
    "/settings/ocr",
    response_model=OcrCapabilityResponse,
    tags=["settings", "library"],
)
def get_ocr_capability(request: Request) -> OcrCapabilityResponse:
    return _ocr_capability_response(_ocr(request).capability())


@router.post(
    "/settings/ocr/models/install",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OcrJobResponse,
    tags=["settings", "jobs"],
    dependencies=[Depends(require_local_mutation)],
)
async def install_ocr_models(
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> OcrJobResponse:
    service = _ocr(request)
    try:
        job = service.create_install_job(idempotency_key=idempotency_key)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"reader-ocr-models:{job.id}",
            lambda: service.install_models(job.id),
        )
    return OcrJobResponse.model_validate(job)


@router.post(
    "/library/works/{work_id}/documents/{document_version_id}/ocr",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OcrJobResponse,
    tags=["library", "jobs"],
    dependencies=[Depends(require_local_mutation)],
)
async def recognize_scanned_document_pages(
    work_id: str,
    document_version_id: str,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> OcrJobResponse:
    service = _ocr(request)
    try:
        job = service.create_recognition_job(
            work_id=work_id,
            document_version_id=document_version_id,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work or document was not found in the local library",
        ) from error
    except OcrError as error:
        raise _ocr_conflict(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"reader-ocr:{job.id}",
            lambda: service.execute_recognition(job.id),
        )
    return OcrJobResponse.model_validate(job)


@router.post(
    "/library/cache-cleanup/preview",
    response_model=CleanupPreviewResponse,
    tags=["library"],
    dependencies=[Depends(require_local_mutation)],
)
def preview_cache_cleanup(
    payload: CleanupPreviewRequest,
    request: Request,
) -> CleanupPreviewResponse:
    preview = _retention(request).preview(
        now=datetime.now(UTC),
        retention_days=payload.retention_days,
    )
    return _cleanup_preview_response(preview)


@router.post(
    "/library/cache-cleanup/apply",
    response_model=CleanupResultResponse,
    tags=["library"],
    dependencies=[Depends(require_local_mutation)],
)
def apply_cache_cleanup(
    payload: CleanupApplyRequest,
    request: Request,
) -> CleanupResultResponse:
    try:
        result: CleanupResult = _retention(request).apply(payload.preview_token)
    except CleanupPreviewError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except UnsafeStoredPathError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cleanup stopped because a stored document path is unsafe",
        ) from error
    return CleanupResultResponse.model_validate(result)


@router.get(
    "/library/works",
    response_model=LibraryPageResponse,
    tags=["library"],
)
def list_library_works(
    request: Request,
    query: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    source: Annotated[str | None, Query(max_length=80)] = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    favorite: bool | None = None,
    unread: bool | None = None,
) -> LibraryPageResponse:
    return _page_response(
        _library(request).list_entries(
            query=query,
            limit=limit,
            offset=offset,
            source=source,
            published_from=published_from,
            published_to=published_to,
            favorite=favorite,
            unread=unread,
        )
    )


@router.get(
    "/works/{work_id}",
    response_model=LibraryWorkResponse,
    tags=["works"],
)
def get_library_work(work_id: str, request: Request) -> LibraryWorkResponse:
    try:
        return _work_response(_library(request).get_work(work_id))
    except KeyError as error:
        raise _not_found(work_id) from error


@router.get(
    "/works/{work_id}/documents",
    response_model=tuple[LibraryDocumentResponse, ...],
    tags=["works"],
)
def list_work_documents(
    work_id: str,
    request: Request,
) -> tuple[LibraryDocumentResponse, ...]:
    try:
        documents = _library(request).list_documents(work_id)
    except KeyError as error:
        raise _not_found(work_id) from error
    return tuple(_document_response(document) for document in documents)


@router.get(
    "/documents/{document_version_id}/content",
    response_class=FileResponse,
    tags=["works", "library"],
)
def get_document_content(
    document_version_id: str,
    request: Request,
) -> FileResponse:
    try:
        content = _content(request).open(document_version_id)
    except DocumentContentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document was not found in the local library",
        ) from error
    except DocumentContentUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Document content is no longer available",
        ) from error
    return FileResponse(
        content.path,
        media_type=content.media_type,
        filename=content.filename,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/documents/{document_version_id}/reading-position",
    response_model=ReadingPositionResponse | None,
    tags=["library", "annotations"],
)
def get_reading_position(
    document_version_id: str,
    request: Request,
) -> ReadingPositionResponse | None:
    try:
        value = _annotations(request).get_position(document_version_id)
    except (AnnotationNotFoundError, AnnotationConflictError) as error:
        raise _annotation_http_error(error) from error
    return _position_response(value) if value is not None else None


@router.put(
    "/documents/{document_version_id}/reading-position",
    response_model=ReadingPositionResponse,
    tags=["library", "annotations"],
    dependencies=[Depends(require_local_mutation)],
)
def save_reading_position(
    document_version_id: str,
    payload: ReadingPositionRequest,
    request: Request,
) -> ReadingPositionResponse:
    try:
        value = _annotations(request).save_position(
            document_version_id,
            _position_input(payload),
        )
    except (AnnotationNotFoundError, AnnotationConflictError, ValueError) as error:
        raise _annotation_http_error(error) from error
    return _position_response(value)


@router.get(
    "/documents/{document_version_id}/annotations",
    response_model=tuple[AnnotationResponse, ...],
    tags=["library", "annotations"],
)
def list_annotations(
    document_version_id: str,
    request: Request,
    kind: Annotated[AnnotationKind | None, Query()] = None,
) -> tuple[AnnotationResponse, ...]:
    try:
        values = _annotations(request).list(document_version_id, kind=kind)
    except (AnnotationNotFoundError, AnnotationConflictError) as error:
        raise _annotation_http_error(error) from error
    return tuple(_annotation_response(value) for value in values)


@router.post(
    "/documents/{document_version_id}/annotations",
    status_code=status.HTTP_201_CREATED,
    response_model=AnnotationResponse,
    tags=["library", "annotations"],
    dependencies=[Depends(require_local_mutation)],
)
def create_annotation(
    document_version_id: str,
    payload: AnnotationRequest,
    request: Request,
) -> AnnotationResponse:
    try:
        value = _annotations(request).create(
            document_version_id,
            _annotation_input(payload),
        )
    except (AnnotationNotFoundError, AnnotationConflictError, ValueError) as error:
        raise _annotation_http_error(error) from error
    return _annotation_response(value)


@router.put(
    "/documents/{document_version_id}/annotations/{annotation_id}",
    response_model=AnnotationResponse,
    tags=["library", "annotations"],
    dependencies=[Depends(require_local_mutation)],
)
def update_annotation(
    document_version_id: str,
    annotation_id: str,
    payload: AnnotationRequest,
    request: Request,
) -> AnnotationResponse:
    try:
        value = _annotations(request).update(
            document_version_id,
            annotation_id,
            _annotation_input(payload),
        )
    except (AnnotationNotFoundError, AnnotationConflictError, ValueError) as error:
        raise _annotation_http_error(error) from error
    return _annotation_response(value)


@router.delete(
    "/documents/{document_version_id}/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["library", "annotations"],
    dependencies=[Depends(require_local_mutation)],
)
def delete_annotation(
    document_version_id: str,
    annotation_id: str,
    request: Request,
) -> Response:
    try:
        _annotations(request).delete(document_version_id, annotation_id)
    except (AnnotationNotFoundError, AnnotationConflictError) as error:
        raise _annotation_http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/documents/{source_document_version_id}/annotation-migrations/preview",
    response_model=MigrationPreviewResponse,
    tags=["library", "annotations"],
    dependencies=[Depends(require_local_mutation)],
)
def preview_annotation_migration(
    source_document_version_id: str,
    payload: AnnotationMigrationPreviewRequest,
    request: Request,
) -> MigrationPreviewResponse:
    try:
        value = _annotation_migrations(request).preview(
            source_document_version_id,
            payload.target_document_version_id,
        )
    except (
        MigrationNotFoundError,
        MigrationConflictError,
        MigrationCancelledError,
        ValueError,
    ) as error:
        raise _migration_http_error(error) from error
    return _migration_preview_response(value)


@router.get(
    "/annotation-migrations/{preview_id}",
    response_model=MigrationPreviewResponse,
    tags=["library", "annotations"],
)
def get_annotation_migration(
    preview_id: str,
    request: Request,
) -> MigrationPreviewResponse:
    try:
        value = _annotation_migrations(request).get(preview_id)
    except (MigrationNotFoundError, MigrationConflictError) as error:
        raise _migration_http_error(error) from error
    return _migration_preview_response(value)


@router.put(
    "/annotation-migrations/candidates/{candidate_id}/decision",
    response_model=MigrationCandidateResponse,
    tags=["library", "annotations"],
    dependencies=[Depends(require_local_mutation)],
)
def decide_annotation_migration(
    candidate_id: str,
    payload: AnnotationMigrationDecisionRequest,
    request: Request,
) -> MigrationCandidateResponse:
    try:
        value = _annotation_migrations(request).decide(
            candidate_id,
            decision=payload.decision,
        )
    except (MigrationNotFoundError, MigrationConflictError, ValueError) as error:
        raise _migration_http_error(error) from error
    return _migration_candidate_response(value)
