"""Page-level native PDF extraction and durable extraction jobs."""

from __future__ import annotations

import asyncio
import math
import time
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from threading import Event
from types import ModuleType
from typing import Any, Literal, Protocol, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import JobRecord, utc_now
from paper_reader.jobs.repository import JobRepository
from paper_reader.reader.models import DocumentPageTextRecord, LibraryEntryRecord
from paper_reader.reader.native_process import NativeProcessError, NativeProcessRunner
from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError

EXTRACTION_JOB_KIND = "reader.extract_document"
DEFAULT_MAX_PAGES = 2_000
DEFAULT_MAX_PAGE_DIMENSION_POINTS = 14_400
DEFAULT_MAX_PAGE_AREA_POINTS = 25_000_000
DEFAULT_MAX_BLOCKS_PER_PAGE = 20_000
DEFAULT_MAX_BLOCKS_TOTAL = 200_000
DEFAULT_MAX_IMAGES_PER_PAGE = 10_000
DEFAULT_MAX_CHARACTERS_PER_PAGE = 2_000_000
DEFAULT_MAX_CHARACTERS_TOTAL = 20_000_000
DEFAULT_MAX_SECONDS = 120.0
SCAN_NATIVE_CHARACTER_THRESHOLD = 32
SCAN_IMAGE_COVERAGE_THRESHOLD = 0.5
LEGAL_ACCESS = frozenset({"open", "user_provided"})

ExtractionErrorCode = Literal[
    "missing_file",
    "encrypted",
    "damaged",
    "too_many_pages",
    "page_too_large",
    "too_many_blocks",
    "too_many_images",
    "too_much_text",
    "time_limit",
    "cancelled",
    "extractor_unavailable",
    "internal_error",
]
OcrState = Literal["not_needed", "pending", "complete", "failed", "unavailable"]
TextSource = Literal["native", "ocr"]
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class TextBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_number: int
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int
    page_width: float
    page_height: float
    text: str
    blocks: tuple[TextBlock, ...]
    native_character_count: int
    likely_scanned: bool
    ocr_state: OcrState


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    pages: tuple[PageText, ...]
    page_count: int
    native_character_count: int
    scanned_page_count: int
    extractor_version: str


@dataclass(frozen=True, slots=True)
class _ExtractedPage:
    page: PageText
    processed_character_count: int


@dataclass(frozen=True, slots=True)
class PageEvidence:
    document_version_id: str
    page_number: int
    page_width: float
    page_height: float
    text: str
    text_source: TextSource
    blocks: tuple[TextBlock, ...]
    native_text: str
    native_blocks: tuple[TextBlock, ...]
    native_character_count: int
    likely_scanned: bool
    ocr_state: OcrState
    ocr_text: str | None
    ocr_blocks: tuple[TextBlock, ...]
    ocr_confidence: float | None
    ocr_engine_version: str | None
    ocr_model_bundle_id: str | None
    extractor_version: str


class ExtractionError(RuntimeError):
    """A stable, user-safe PDF extraction failure."""

    def __init__(
        self,
        code: ExtractionErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _ExtractionCallbackError(RuntimeError):
    """A caller callback failed; this is not evidence that the PDF is damaged."""


class ExtractionBackend(Protocol):
    def extract(
        self,
        document: Path,
        *,
        cancelled: Callable[[], bool],
        progress: ProgressCallback = lambda _completed, _total: None,
    ) -> ExtractionResult: ...


def _load_pymupdf() -> ModuleType:
    return import_module("pymupdf")


class PdfTextExtractor:
    """Extract native text once per page without invoking any OCR runtime."""

    def __init__(
        self,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_page_dimension_points: float = DEFAULT_MAX_PAGE_DIMENSION_POINTS,
        max_page_area_points: float = DEFAULT_MAX_PAGE_AREA_POINTS,
        max_blocks_per_page: int = DEFAULT_MAX_BLOCKS_PER_PAGE,
        max_blocks_total: int = DEFAULT_MAX_BLOCKS_TOTAL,
        max_images_per_page: int = DEFAULT_MAX_IMAGES_PER_PAGE,
        max_characters_per_page: int = DEFAULT_MAX_CHARACTERS_PER_PAGE,
        max_characters_total: int = DEFAULT_MAX_CHARACTERS_TOTAL,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        module_loader: Callable[[], ModuleType] = _load_pymupdf,
    ) -> None:
        limits = {
            "max_pages": max_pages,
            "max_page_dimension_points": max_page_dimension_points,
            "max_page_area_points": max_page_area_points,
            "max_blocks_per_page": max_blocks_per_page,
            "max_blocks_total": max_blocks_total,
            "max_images_per_page": max_images_per_page,
            "max_characters_per_page": max_characters_per_page,
            "max_characters_total": max_characters_total,
            "max_seconds": max_seconds,
        }
        for name, value in limits.items():
            if isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be positive")
        self._max_pages = max_pages
        self._max_page_dimension_points = float(max_page_dimension_points)
        self._max_page_area_points = float(max_page_area_points)
        self._max_blocks_per_page = max_blocks_per_page
        self._max_blocks_total = max_blocks_total
        self._max_images_per_page = max_images_per_page
        self._max_characters_per_page = max_characters_per_page
        self._max_characters_total = max_characters_total
        self._max_seconds = float(max_seconds)
        self._clock = clock
        self._module_loader = module_loader

    def extract(
        self,
        document: Path,
        *,
        cancelled: Callable[[], bool],
        progress: ProgressCallback = lambda _completed, _total: None,
    ) -> ExtractionResult:
        path = Path(document)
        deadline = self._clock() + self._max_seconds
        if _callback_cancelled(cancelled):
            raise _cancelled_error()
        self._check_runtime(cancelled=cancelled, deadline=deadline)
        if not path.is_file():
            raise ExtractionError(
                "missing_file",
                "The local PDF file is unavailable",
                retryable=True,
            )
        try:
            module: Any = self._module_loader()
        except (ImportError, OSError, RuntimeError) as error:
            raise ExtractionError(
                "extractor_unavailable",
                "The native PDF extraction runtime is unavailable",
                retryable=True,
            ) from error

        try:
            opened: Any = module.open(path)
        except Exception as error:
            if not path.is_file():
                raise ExtractionError(
                    "missing_file",
                    "The local PDF file disappeared before extraction",
                    retryable=True,
                ) from error
            raise _damaged_error() from error

        try:
            with opened as pdf:
                self._check_runtime(cancelled=cancelled, deadline=deadline)
                if bool(pdf.needs_pass):
                    raise ExtractionError(
                        "encrypted",
                        "The PDF requires a password before it can be read",
                        retryable=False,
                    )
                if bool(getattr(pdf, "is_repaired", False)):
                    raise _damaged_error()
                page_count = int(pdf.page_count)
                if page_count <= 0:
                    raise _damaged_error()
                if page_count > self._max_pages:
                    raise ExtractionError(
                        "too_many_pages",
                        f"The PDF exceeds the {self._max_pages}-page extraction limit",
                        retryable=False,
                    )

                pages: list[PageText] = []
                character_total = 0
                block_total = 0
                for page_index in range(page_count):
                    self._check_runtime(cancelled=cancelled, deadline=deadline)
                    try:
                        page = pdf.load_page(page_index)
                        self._check_runtime(cancelled=cancelled, deadline=deadline)
                        extracted = self._extract_page(
                            page,
                            page_number=page_index + 1,
                            cancelled=cancelled,
                            deadline=deadline,
                        )
                    except ExtractionError:
                        raise
                    except Exception as error:
                        raise _damaged_error() from error
                    character_total += extracted.processed_character_count
                    if character_total > self._max_characters_total:
                        raise _too_much_text_error()
                    block_total += len(extracted.page.blocks)
                    if block_total > self._max_blocks_total:
                        raise _too_many_blocks_error()
                    pages.append(extracted.page)
                    _callback_progress(progress, page_index + 1, page_count)
                    self._check_runtime(cancelled=cancelled, deadline=deadline)
        except _ExtractionCallbackError:
            raise
        except ExtractionError:
            raise
        except Exception as error:
            raise _damaged_error() from error

        version = str(getattr(module, "__version__", "unknown"))
        extractor_version = f"pymupdf-{version}/native-v1"[:80]
        result_pages = tuple(pages)
        return ExtractionResult(
            pages=result_pages,
            page_count=len(result_pages),
            native_character_count=sum(page.native_character_count for page in result_pages),
            scanned_page_count=sum(page.likely_scanned for page in result_pages),
            extractor_version=extractor_version,
        )

    def _extract_page(
        self,
        page: Any,
        *,
        page_number: int,
        cancelled: Callable[[], bool],
        deadline: float,
    ) -> _ExtractedPage:
        width = float(page.rect.width)
        height = float(page.rect.height)
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            raise _damaged_error()
        if (
            width > self._max_page_dimension_points
            or height > self._max_page_dimension_points
            or width * height > self._max_page_area_points
        ):
            raise ExtractionError(
                "page_too_large",
                "The PDF page dimensions exceed the safe extraction limit",
                retryable=False,
            )

        self._check_runtime(cancelled=cancelled, deadline=deadline)
        raw_blocks = cast(list[Any], page.get_text("blocks", sort=True))
        self._check_runtime(cancelled=cancelled, deadline=deadline)
        if len(raw_blocks) > self._max_blocks_per_page:
            raise _too_many_blocks_error()
        blocks: list[TextBlock] = []
        processed_character_count = 0
        native_character_count = 0
        for index, raw in enumerate(raw_blocks):
            if index % 128 == 0:
                self._check_runtime(cancelled=cancelled, deadline=deadline)
            if not isinstance(raw, (tuple, list)) or len(raw) < 7:
                raise _damaged_error()
            if int(raw[6]) != 0:
                continue
            text = _normalize_text(str(raw[4]))
            if not text:
                continue
            processed_character_count += len(text)
            if processed_character_count > self._max_characters_per_page:
                raise _too_much_text_error()
            coordinates = _bounded_box(raw[:4], width=width, height=height)
            if coordinates is None:
                continue
            native_character_count += sum(not character.isspace() for character in text)
            blocks.append(
                TextBlock(
                    x0=coordinates[0],
                    y0=coordinates[1],
                    x1=coordinates[2],
                    y1=coordinates[3],
                    text=text,
                    block_number=int(raw[5]),
                )
            )

        native_text = "".join(block.text for block in blocks)
        self._check_runtime(cancelled=cancelled, deadline=deadline)
        likely_scanned = False
        if native_character_count < SCAN_NATIVE_CHARACTER_THRESHOLD:
            image_coverage = _image_coverage(
                page,
                width=width,
                height=height,
                max_images=self._max_images_per_page,
                check_runtime=lambda: self._check_runtime(
                    cancelled=cancelled,
                    deadline=deadline,
                ),
            )
            likely_scanned = image_coverage >= SCAN_IMAGE_COVERAGE_THRESHOLD
        return _ExtractedPage(
            page=PageText(
                page_number=page_number,
                page_width=_stable_float(width),
                page_height=_stable_float(height),
                text=native_text,
                blocks=tuple(blocks),
                native_character_count=native_character_count,
                likely_scanned=likely_scanned,
                ocr_state="pending" if likely_scanned else "not_needed",
            ),
            processed_character_count=processed_character_count,
        )

    def _check_runtime(
        self,
        *,
        cancelled: Callable[[], bool],
        deadline: float,
    ) -> None:
        if _callback_cancelled(cancelled):
            raise _cancelled_error()
        if self._clock() > deadline:
            raise ExtractionError(
                "time_limit",
                "The PDF exceeded the safe extraction time limit",
                retryable=False,
            )


class IsolatedPdfTextExtractor:
    """Run PyMuPDF in a hard-terminable, credential-free child process."""

    def __init__(
        self,
        *,
        work_root: Path,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_page_dimension_points: float = DEFAULT_MAX_PAGE_DIMENSION_POINTS,
        max_page_area_points: float = DEFAULT_MAX_PAGE_AREA_POINTS,
        max_blocks_per_page: int = DEFAULT_MAX_BLOCKS_PER_PAGE,
        max_blocks_total: int = DEFAULT_MAX_BLOCKS_TOTAL,
        max_images_per_page: int = DEFAULT_MAX_IMAGES_PER_PAGE,
        max_characters_per_page: int = DEFAULT_MAX_CHARACTERS_PER_PAGE,
        max_characters_total: int = DEFAULT_MAX_CHARACTERS_TOTAL,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        runner: NativeProcessRunner | None = None,
    ) -> None:
        # Reuse the in-process constructor as the canonical limit validator.
        PdfTextExtractor(
            max_pages=max_pages,
            max_page_dimension_points=max_page_dimension_points,
            max_page_area_points=max_page_area_points,
            max_blocks_per_page=max_blocks_per_page,
            max_blocks_total=max_blocks_total,
            max_images_per_page=max_images_per_page,
            max_characters_per_page=max_characters_per_page,
            max_characters_total=max_characters_total,
            max_seconds=max_seconds,
        )
        self._limits: dict[str, int | float] = {
            "max_pages": max_pages,
            "max_page_dimension_points": float(max_page_dimension_points),
            "max_page_area_points": float(max_page_area_points),
            "max_blocks_per_page": max_blocks_per_page,
            "max_blocks_total": max_blocks_total,
            "max_images_per_page": max_images_per_page,
            "max_characters_per_page": max_characters_per_page,
            "max_characters_total": max_characters_total,
            "max_seconds": float(max_seconds),
        }
        self._max_seconds = float(max_seconds)
        self._runner = runner or NativeProcessRunner(work_root=work_root)

    def extract(
        self,
        document: Path,
        *,
        cancelled: Callable[[], bool],
        progress: ProgressCallback = lambda _completed, _total: None,
    ) -> ExtractionResult:
        try:
            envelope = self._runner.run(
                operation="pdf_extract",
                payload={
                    "document": str(Path(document).resolve()),
                    "limits": dict(self._limits),
                },
                timeout_seconds=self._max_seconds,
                cancelled=cancelled,
                progress=progress,
            )
        except NativeProcessError as error:
            if error.code == "cancelled":
                raise _cancelled_error() from error
            if error.code == "time_limit":
                raise ExtractionError(
                    "time_limit",
                    "The document exceeded the safe PDF extraction time limit",
                    retryable=False,
                ) from error
            if error.code == "worker_unavailable":
                raise ExtractionError(
                    "extractor_unavailable",
                    "The native PDF extraction runtime is unavailable",
                    retryable=True,
                ) from error
            raise ExtractionError(
                "internal_error",
                "The isolated PDF extraction worker failed safely",
                retryable=True,
            ) from error
        return _isolated_extraction_result(envelope, limits=self._limits)


class DocumentExtractionService:
    """Run extraction off the event loop and publish pages atomically."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        jobs: JobRepository,
        store: ContentStore,
        extractor: ExtractionBackend,
    ) -> None:
        self._sessions = sessions
        self._jobs = jobs
        self._store = store
        self._extractor = extractor

    def create_job(
        self,
        *,
        work_id: str,
        document_version_id: str,
        idempotency_key: str,
    ) -> JobRecord:
        key = idempotency_key.strip()
        if not key or len(key) > 160:
            raise ValueError("idempotency key must contain 1-160 characters")
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            if session.get(LibraryEntryRecord, work.id) is None:
                raise KeyError(work_id)
            document = session.get(DocumentVersionRecord, document_version_id)
            if document is None or document.work_id != work.id:
                raise KeyError(document_version_id)
            if (
                document.access_status not in LEGAL_ACCESS
                or document.media_type.casefold() != "application/pdf"
                or document.relative_path is None
            ):
                raise ValueError("Document is not a locally available readable PDF")
            try:
                path = self._store.resolve_object(
                    document.content_sha256,
                    document.relative_path,
                )
            except UnsafeStoredPathError as error:
                raise ValueError("Document is not a locally available readable PDF") from error
            if not path.is_file():
                raise ValueError("Document is not a locally available readable PDF")
            payload = {
                "work_id": work.id,
                "document_version_id": document.id,
                "content_sha256": document.content_sha256,
            }
        job = self._jobs.create(
            kind=EXTRACTION_JOB_KIND,
            payload=payload,
            idempotency_key=key,
        )
        if job.kind != EXTRACTION_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different extraction command")
        return job

    async def execute(self, job_id: str) -> None:
        job = self._require_job(job_id)
        payload = self._read_payload(job.payload)
        if job.status in {"succeeded", "cancelled"}:
            return
        checkpoint: dict[str, Any] = {
            "document_version_id": payload["document_version_id"],
            "completed_pages": 0,
            "page_count": None,
        }
        if job.cancel_requested:
            self._set_document_status(payload, "pending")
            self._finish_cancelled(job.id, progress=job.progress, checkpoint=checkpoint)
            return

        self._set_document_status(payload, "running")
        self._jobs.update_state(
            job.id,
            status="running",
            progress=0,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.extraction.started",
            progress=0,
            payload={
                "work_id": payload["work_id"],
                "document_version_id": payload["document_version_id"],
            },
        )
        current_progress = 0
        stop_for_shutdown = Event()

        def cancelled() -> bool:
            if stop_for_shutdown.is_set():
                return True
            current = self._jobs.get(job.id)
            return current is None or current.cancel_requested

        def report_progress(completed: int, total: int) -> None:
            nonlocal checkpoint, current_progress
            current_progress = min(90, max(1, int(completed * 90 / total)))
            checkpoint = {
                "document_version_id": payload["document_version_id"],
                "completed_pages": completed,
                "page_count": total,
            }
            self._jobs.update_state(
                job.id,
                status="running",
                progress=current_progress,
                checkpoint=checkpoint,
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="reader.extraction.progress",
                progress=current_progress,
                payload=dict(checkpoint),
            )

        try:
            path = self._document_path(payload)
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._extractor.extract,
                    path,
                    cancelled=cancelled,
                    progress=report_progress,
                )
            )
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                stop_for_shutdown.set()
                with suppress(Exception):
                    await worker
                raise
            if cancelled():
                raise _cancelled_error()
            self._persist_result(payload, result)
        except ExtractionError as error:
            if error.code == "cancelled":
                self._set_document_status(payload, "pending")
                self._finish_cancelled(
                    job.id,
                    progress=current_progress,
                    checkpoint=checkpoint,
                )
            else:
                self._set_document_status(payload, "failed")
                self._finish_failed(
                    job.id,
                    progress=current_progress,
                    checkpoint=checkpoint,
                    error=error,
                )
            return
        except Exception:
            self._set_document_status(payload, "failed")
            self._finish_failed(
                job.id,
                progress=current_progress,
                checkpoint=checkpoint,
                error=ExtractionError(
                    "internal_error",
                    "The PDF extraction failed unexpectedly",
                    retryable=False,
                ),
            )
            return

        response = {
            "work_id": payload["work_id"],
            "document_version_id": payload["document_version_id"],
            "page_count": result.page_count,
            "native_character_count": result.native_character_count,
            "scanned_page_count": result.scanned_page_count,
            "extractor_version": result.extractor_version,
        }
        final_checkpoint = {
            "document_version_id": payload["document_version_id"],
            "completed_pages": result.page_count,
            "page_count": result.page_count,
            "extractor_version": result.extractor_version,
        }
        self._jobs.update_state(
            job.id,
            status="succeeded",
            progress=100,
            result=response,
            checkpoint=final_checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.extraction.succeeded",
            progress=100,
            payload=response,
        )

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=EXTRACTION_JOB_KIND)

    def list_pages(
        self,
        *,
        work_id: str,
        document_version_id: str,
    ) -> tuple[PageEvidence, ...]:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            if session.get(LibraryEntryRecord, work.id) is None:
                raise KeyError(work_id)
            document = session.get(DocumentVersionRecord, document_version_id)
            if (
                document is None
                or document.work_id != work.id
                or document.access_status not in LEGAL_ACCESS
                or document.media_type.casefold() != "application/pdf"
            ):
                raise KeyError(document_version_id)
            rows = list(
                session.scalars(
                    select(DocumentPageTextRecord)
                    .where(DocumentPageTextRecord.document_version_id == document_version_id)
                    .order_by(DocumentPageTextRecord.page_number)
                )
            )
            return tuple(self._page_evidence(row) for row in rows)

    def _document_path(self, payload: Mapping[str, str]) -> Path:
        with self._sessions() as session:
            document = session.get(
                DocumentVersionRecord,
                payload["document_version_id"],
            )
            if (
                document is None
                or document.work_id != payload["work_id"]
                or document.content_sha256 != payload["content_sha256"]
                or document.relative_path is None
                or document.access_status not in LEGAL_ACCESS
                or document.media_type.casefold() != "application/pdf"
            ):
                raise ExtractionError(
                    "missing_file",
                    "The local PDF is no longer available for extraction",
                    retryable=True,
                )
            try:
                path = self._store.resolve_object(
                    document.content_sha256,
                    document.relative_path,
                )
            except UnsafeStoredPathError as error:
                raise _damaged_error() from error
        if not path.is_file():
            raise ExtractionError(
                "missing_file",
                "The local PDF file is missing",
                retryable=True,
            )
        return path

    def _persist_result(
        self,
        payload: Mapping[str, str],
        result: ExtractionResult,
    ) -> None:
        _validate_result(result)
        observed_at = utc_now()
        with self._sessions() as session:
            document = session.get(
                DocumentVersionRecord,
                payload["document_version_id"],
            )
            if (
                document is None
                or document.work_id != payload["work_id"]
                or document.content_sha256 != payload["content_sha256"]
                or document.relative_path is None
                or document.access_status not in LEGAL_ACCESS
                or document.media_type.casefold() != "application/pdf"
            ):
                raise ExtractionError(
                    "missing_file",
                    "The document changed before extracted pages could be saved",
                    retryable=True,
                )
            session.execute(
                delete(DocumentPageTextRecord).where(
                    DocumentPageTextRecord.document_version_id == document.id
                )
            )
            session.add_all(
                [
                    DocumentPageTextRecord(
                        document_version_id=document.id,
                        page_number=page.page_number,
                        page_width=page.page_width,
                        page_height=page.page_height,
                        native_text=page.text,
                        native_blocks=[_block_payload(block) for block in page.blocks],
                        native_character_count=page.native_character_count,
                        likely_scanned=page.likely_scanned,
                        ocr_state=page.ocr_state,
                        ocr_text=None,
                        ocr_blocks=[],
                        ocr_confidence=None,
                        ocr_engine_version=None,
                        ocr_model_bundle_id=None,
                        extractor_version=result.extractor_version,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                    for page in result.pages
                ]
            )
            document.extraction_status = "complete"
            session.commit()

    def _set_document_status(
        self,
        payload: Mapping[str, str],
        value: str,
    ) -> None:
        with self._sessions() as session:
            document = session.get(
                DocumentVersionRecord,
                payload["document_version_id"],
            )
            if (
                document is not None
                and document.work_id == payload["work_id"]
                and document.content_sha256 == payload["content_sha256"]
            ):
                document.extraction_status = value
                session.commit()

    def _finish_cancelled(
        self,
        job_id: str,
        *,
        progress: int,
        checkpoint: dict[str, Any],
    ) -> None:
        result = {"cancelled": True}
        self._jobs.update_state(
            job_id,
            status="cancelled",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code="cancelled",
            error_message="The PDF extraction was cancelled",
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="reader.extraction.cancelled",
            progress=progress,
            payload=result,
        )

    def _finish_failed(
        self,
        job_id: str,
        *,
        progress: int,
        checkpoint: dict[str, Any],
        error: ExtractionError,
    ) -> None:
        result = {"retryable": error.retryable}
        self._jobs.update_state(
            job_id,
            status="failed",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code=error.code,
            error_message=str(error),
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type="reader.extraction.failed",
            progress=progress,
            payload={"code": error.code, "retryable": error.retryable},
        )

    def _require_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != EXTRACTION_JOB_KIND:
            raise ValueError("job is not a document extraction command")
        return job

    @staticmethod
    def _read_payload(value: Mapping[str, Any]) -> dict[str, str]:
        keys = ("work_id", "document_version_id", "content_sha256")
        if any(not isinstance(value.get(key), str) for key in keys):
            raise ValueError("document extraction job payload is invalid")
        return {key: cast(str, value[key]) for key in keys}

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

    @staticmethod
    def _page_evidence(row: DocumentPageTextRecord) -> PageEvidence:
        native_blocks = tuple(_block_from_payload(value) for value in row.native_blocks)
        ocr_blocks = tuple(_block_from_payload(value) for value in row.ocr_blocks)
        use_ocr = row.ocr_state == "complete" and row.ocr_text is not None
        effective_text = cast(str, row.ocr_text) if use_ocr else row.native_text
        return PageEvidence(
            document_version_id=row.document_version_id,
            page_number=row.page_number,
            page_width=row.page_width,
            page_height=row.page_height,
            text=effective_text,
            text_source="ocr" if use_ocr else "native",
            blocks=ocr_blocks if use_ocr else native_blocks,
            native_text=row.native_text,
            native_blocks=native_blocks,
            native_character_count=row.native_character_count,
            likely_scanned=row.likely_scanned,
            ocr_state=cast(OcrState, row.ocr_state),
            ocr_text=row.ocr_text,
            ocr_blocks=ocr_blocks,
            ocr_confidence=row.ocr_confidence,
            ocr_engine_version=row.ocr_engine_version,
            ocr_model_bundle_id=row.ocr_model_bundle_id,
            extractor_version=row.extractor_version,
        )


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _isolated_extraction_result(
    envelope: Mapping[str, Any],
    *,
    limits: Mapping[str, int | float],
) -> ExtractionResult:
    if envelope.get("ok") is False:
        error = envelope.get("error")
        if set(envelope) != {"ok", "error"} or not isinstance(error, dict):
            raise _isolated_protocol_error()
        code = error.get("code")
        policy: dict[str, tuple[ExtractionErrorCode, str, bool]] = {
            "missing_file": ("missing_file", "The local PDF file is unavailable", True),
            "encrypted": (
                "encrypted",
                "The PDF requires a password before it can be read",
                False,
            ),
            "damaged": ("damaged", "The PDF could not be parsed safely", False),
            "too_many_pages": (
                "too_many_pages",
                "The PDF exceeds the safe page limit",
                False,
            ),
            "page_too_large": (
                "page_too_large",
                "The PDF page dimensions exceed the safe extraction limit",
                False,
            ),
            "too_many_blocks": (
                "too_many_blocks",
                "The PDF contains too many text blocks to process safely",
                False,
            ),
            "too_many_images": (
                "too_many_images",
                "The PDF page contains too many images to inspect safely",
                False,
            ),
            "too_much_text": (
                "too_much_text",
                "The PDF contains too much decoded text to process safely",
                False,
            ),
            "time_limit": (
                "time_limit",
                "The PDF exceeded the safe extraction time limit",
                False,
            ),
            "cancelled": ("cancelled", "The PDF extraction was cancelled", True),
            "extractor_unavailable": (
                "extractor_unavailable",
                "The native PDF extraction runtime is unavailable",
                True,
            ),
            "internal_error": (
                "internal_error",
                "The isolated PDF extraction worker failed safely",
                True,
            ),
            "result_limit": (
                "internal_error",
                "The isolated PDF extraction result exceeded its safe limit",
                False,
            ),
        }
        selected = policy.get(code) if isinstance(code, str) else None
        if selected is None:
            raise _isolated_protocol_error()
        raise ExtractionError(selected[0], selected[1], retryable=selected[2])
    result = envelope.get("result")
    if set(envelope) != {"ok", "result"} or envelope.get("ok") is not True:
        raise _isolated_protocol_error()
    if not isinstance(result, dict) or set(result) != {
        "pages",
        "page_count",
        "native_character_count",
        "scanned_page_count",
        "extractor_version",
    }:
        raise _isolated_protocol_error()
    raw_pages = result.get("pages")
    page_count = result.get("page_count")
    native_character_count = result.get("native_character_count")
    scanned_page_count = result.get("scanned_page_count")
    extractor_version = result.get("extractor_version")
    max_pages = int(limits["max_pages"])
    if (
        not isinstance(raw_pages, list)
        or isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or not 1 <= page_count <= max_pages
        or len(raw_pages) != page_count
        or isinstance(native_character_count, bool)
        or not isinstance(native_character_count, int)
        or native_character_count < 0
        or isinstance(scanned_page_count, bool)
        or not isinstance(scanned_page_count, int)
        or not 0 <= scanned_page_count <= page_count
        or not isinstance(extractor_version, str)
        or not 1 <= len(extractor_version) <= 80
        or any(ord(character) < 32 for character in extractor_version)
    ):
        raise _isolated_protocol_error()

    pages: list[PageText] = []
    character_total = 0
    block_total = 0
    for expected_page_number, raw_page in enumerate(raw_pages, start=1):
        if not isinstance(raw_page, dict) or set(raw_page) != {
            "page_number",
            "page_width",
            "page_height",
            "text",
            "blocks",
            "native_character_count",
            "likely_scanned",
            "ocr_state",
        }:
            raise _isolated_protocol_error()
        page_number = raw_page.get("page_number")
        width = _isolated_number(raw_page.get("page_width"))
        height = _isolated_number(raw_page.get("page_height"))
        text = raw_page.get("text")
        raw_blocks = raw_page.get("blocks")
        page_characters = raw_page.get("native_character_count")
        likely_scanned = raw_page.get("likely_scanned")
        ocr_state = raw_page.get("ocr_state")
        if (
            page_number != expected_page_number
            or width <= 0
            or height <= 0
            or width > float(limits["max_page_dimension_points"])
            or height > float(limits["max_page_dimension_points"])
            or width * height > float(limits["max_page_area_points"])
            or not isinstance(text, str)
            or len(text) > int(limits["max_characters_per_page"])
            or not isinstance(raw_blocks, list)
            or len(raw_blocks) > int(limits["max_blocks_per_page"])
            or isinstance(page_characters, bool)
            or not isinstance(page_characters, int)
            or page_characters < 0
            or not isinstance(likely_scanned, bool)
            or ocr_state not in {"not_needed", "pending"}
            or (likely_scanned and ocr_state != "pending")
            or (not likely_scanned and ocr_state != "not_needed")
        ):
            raise _isolated_protocol_error()
        block_total += len(raw_blocks)
        if block_total > int(limits["max_blocks_total"]):
            raise _isolated_protocol_error()
        blocks: list[TextBlock] = []
        for raw_block in raw_blocks:
            if not isinstance(raw_block, dict) or set(raw_block) != {
                "x0",
                "y0",
                "x1",
                "y1",
                "text",
                "block_number",
                "confidence",
            }:
                raise _isolated_protocol_error()
            x0 = _isolated_number(raw_block.get("x0"))
            y0 = _isolated_number(raw_block.get("y0"))
            x1 = _isolated_number(raw_block.get("x1"))
            y1 = _isolated_number(raw_block.get("y1"))
            block_text = raw_block.get("text")
            block_number = raw_block.get("block_number")
            if (
                not 0 <= x0 < x1 <= width
                or not 0 <= y0 < y1 <= height
                or not isinstance(block_text, str)
                or isinstance(block_number, bool)
                or not isinstance(block_number, int)
                or block_number < 0
                or raw_block.get("confidence") is not None
            ):
                raise _isolated_protocol_error()
            blocks.append(
                TextBlock(
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    text=block_text,
                    block_number=block_number,
                )
            )
        if (
            "".join(block.text for block in blocks) != text
            or sum(not character.isspace() for character in text) != page_characters
        ):
            raise _isolated_protocol_error()
        character_total += len(text)
        if character_total > int(limits["max_characters_total"]):
            raise _isolated_protocol_error()
        pages.append(
            PageText(
                page_number=expected_page_number,
                page_width=width,
                page_height=height,
                text=text,
                blocks=tuple(blocks),
                native_character_count=page_characters,
                likely_scanned=likely_scanned,
                ocr_state=cast(OcrState, ocr_state),
            )
        )
    if (
        sum(page.native_character_count for page in pages) != native_character_count
        or sum(page.likely_scanned for page in pages) != scanned_page_count
    ):
        raise _isolated_protocol_error()
    return ExtractionResult(
        pages=tuple(pages),
        page_count=page_count,
        native_character_count=native_character_count,
        scanned_page_count=scanned_page_count,
        extractor_version=extractor_version,
    )


def _isolated_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _isolated_protocol_error()
    number = float(value)
    if not math.isfinite(number):
        raise _isolated_protocol_error()
    return number


def _isolated_protocol_error() -> ExtractionError:
    return ExtractionError(
        "internal_error",
        "The isolated PDF extraction worker returned an invalid result",
        retryable=False,
    )


def _bounded_box(
    values: Any,
    *,
    width: float,
    height: float,
) -> tuple[float, float, float, float] | None:
    try:
        x0, y0, x1, y1 = (float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise _damaged_error() from error
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        raise _damaged_error()
    bounded = (
        _stable_float(max(0.0, min(width, x0))),
        _stable_float(max(0.0, min(height, y0))),
        _stable_float(max(0.0, min(width, x1))),
        _stable_float(max(0.0, min(height, y1))),
    )
    if bounded[0] >= bounded[2] or bounded[1] >= bounded[3]:
        return None
    return bounded


def _image_coverage(
    page: Any,
    *,
    width: float,
    height: float,
    max_images: int,
    check_runtime: Callable[[], None],
) -> float:
    page_area = width * height
    covered = 0.0
    check_runtime()
    image_info = cast(
        list[dict[str, Any]],
        page.get_image_info(hashes=False, xrefs=False),
    )
    check_runtime()
    if len(image_info) > max_images:
        raise ExtractionError(
            "too_many_images",
            "The PDF page contains too many images to process safely",
            retryable=False,
        )
    for index, image in enumerate(image_info):
        if index % 128 == 0:
            check_runtime()
        coordinates = _bounded_box(image.get("bbox"), width=width, height=height)
        if coordinates is None:
            continue
        covered += (coordinates[2] - coordinates[0]) * (coordinates[3] - coordinates[1])
    return min(1.0, covered / page_area)


def _stable_float(value: float) -> float:
    return round(value, 3)


def _block_payload(block: TextBlock) -> dict[str, object]:
    payload: dict[str, object] = {
        "x0": block.x0,
        "y0": block.y0,
        "x1": block.x1,
        "y1": block.y1,
        "text": block.text,
        "block_number": block.block_number,
    }
    if block.confidence is not None:
        payload["confidence"] = block.confidence
    return payload


def _block_from_payload(value: Mapping[str, object]) -> TextBlock:
    try:
        raw_confidence = value.get("confidence")
        confidence = None if raw_confidence is None else _stored_float(raw_confidence)
        if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("Stored block confidence is outside 0-1")
        return TextBlock(
            x0=_stored_float(value["x0"]),
            y0=_stored_float(value["y0"]),
            x1=_stored_float(value["x1"]),
            y1=_stored_float(value["y1"]),
            text=str(value["text"]),
            block_number=int(cast(int | str, value["block_number"])),
            confidence=confidence,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Stored page text block is invalid") from error


def _stored_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError("Stored page coordinate is not numeric")
    return float(value)


def _validate_result(result: ExtractionResult) -> None:
    if (
        result.page_count != len(result.pages)
        or result.page_count <= 0
        or tuple(page.page_number for page in result.pages)
        != tuple(range(1, result.page_count + 1))
        or result.native_character_count
        != sum(page.native_character_count for page in result.pages)
        or result.scanned_page_count != sum(page.likely_scanned for page in result.pages)
        or not result.extractor_version
        or len(result.extractor_version) > 80
    ):
        raise RuntimeError("Extractor returned an inconsistent page result")


def _callback_cancelled(callback: Callable[[], bool]) -> bool:
    try:
        return callback()
    except Exception as error:
        raise _ExtractionCallbackError(str(error)) from error


def _callback_progress(
    callback: ProgressCallback,
    completed: int,
    total: int,
) -> None:
    try:
        callback(completed, total)
    except Exception as error:
        raise _ExtractionCallbackError(str(error)) from error


def _cancelled_error() -> ExtractionError:
    return ExtractionError(
        "cancelled",
        "The PDF extraction was cancelled",
        retryable=False,
    )


def _damaged_error() -> ExtractionError:
    return ExtractionError(
        "damaged",
        "The PDF is damaged or cannot be parsed safely",
        retryable=False,
    )


def _too_many_blocks_error() -> ExtractionError:
    return ExtractionError(
        "too_many_blocks",
        "The PDF contains too many text blocks to process safely",
        retryable=False,
    )


def _too_much_text_error() -> ExtractionError:
    return ExtractionError(
        "too_much_text",
        "The PDF contains too much text to process safely",
        retryable=False,
    )
