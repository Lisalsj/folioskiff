"""Durable OCR jobs, persistence, and atomic recognition orchestration."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import DocumentVersionRecord, WorkRecord
from paper_reader.jobs.models import JobRecord, utc_now
from paper_reader.jobs.repository import JobRepository
from paper_reader.reader.models import DocumentPageTextRecord, LibraryEntryRecord
from paper_reader.reader.ocr_contracts import (
    LEGAL_DOCUMENT_ACCESS,
    OCR_MAX_CHARACTERS_PER_PAGE,
    OCR_MAX_CHARACTERS_TOTAL,
    OCR_MAX_IMAGE_BYTES,
    OCR_MAX_LINES_PER_PAGE,
    OCR_MAX_LINES_TOTAL,
    OCR_MAX_RENDER_PIXELS,
    OCR_MAX_SCANNED_PAGES,
    OCR_MAX_SECONDS,
    OCR_MODEL_INSTALL_JOB_KIND,
    OCR_RECOGNITION_JOB_KIND,
    OCR_RENDER_SCALE,
    OcrAssetLifecycle,
    OcrBackend,
    OcrCapability,
    OcrDocumentProcessor,
    OcrError,
    OcrModelBundle,
    OcrModelDownloadError,
    OcrPageResult,
    OcrResult,
    RecognitionProgress,
    RuntimeProbe,
    _DocumentSnapshot,
    _invalid_output_error,
    _ocr_line_limit_error,
    _ocr_text_limit_error,
    _recognition_cancelled_error,
)
from paper_reader.reader.ocr_detection import (
    _aggregate_page_confidence,
    _normalized_page_result,
    _ocr_block_payload,
    scanned_page_snapshots,
    select_scanned_page_rows,
)
from paper_reader.reader.ocr_runtime import (
    OcrModelInstaller,
    _cancelled_download_error,
    inspect_ocr_capability,
    probe_ocr_runtime,
)
from paper_reader.reader.storage import ContentStore, UnsafeStoredPathError


class OcrService:
    """Coordinate explicit model installation and atomic scanned-page OCR."""

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        jobs: JobRepository,
        store: ContentStore,
        bundle: OcrModelBundle,
        installer: OcrModelInstaller,
        backend: OcrBackend,
        document_processor: OcrDocumentProcessor | None = None,
        runtime_probe: RuntimeProbe = lambda: probe_ocr_runtime(),
        asset_service: OcrAssetLifecycle | None = None,
        max_scanned_pages: int = OCR_MAX_SCANNED_PAGES,
        max_render_pixels: int = OCR_MAX_RENDER_PIXELS,
        max_image_bytes: int = OCR_MAX_IMAGE_BYTES,
        max_lines_per_page: int = OCR_MAX_LINES_PER_PAGE,
        max_lines_total: int = OCR_MAX_LINES_TOTAL,
        max_characters_per_page: int = OCR_MAX_CHARACTERS_PER_PAGE,
        max_characters_total: int = OCR_MAX_CHARACTERS_TOTAL,
        max_seconds: float = OCR_MAX_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        limits = {
            "max_scanned_pages": max_scanned_pages,
            "max_render_pixels": max_render_pixels,
            "max_image_bytes": max_image_bytes,
            "max_lines_per_page": max_lines_per_page,
            "max_lines_total": max_lines_total,
            "max_characters_per_page": max_characters_per_page,
            "max_characters_total": max_characters_total,
            "max_seconds": max_seconds,
        }
        for name, value in limits.items():
            if isinstance(value, bool) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be positive")
        self._sessions = sessions
        self._jobs = jobs
        self._store = store
        self._bundle = bundle
        self._installer = installer
        self._backend = backend
        self._document_processor = document_processor
        self._runtime_probe = runtime_probe
        self._asset_service = asset_service
        self._max_scanned_pages = max_scanned_pages
        self._max_render_pixels = max_render_pixels
        self._max_image_bytes = max_image_bytes
        self._max_lines_per_page = max_lines_per_page
        self._max_lines_total = max_lines_total
        self._max_characters_per_page = max_characters_per_page
        self._max_characters_total = max_characters_total
        self._max_seconds = float(max_seconds)
        self._clock = clock

    def capability(self) -> OcrCapability:
        capability = inspect_ocr_capability(
            bundle=self._bundle,
            installer=self._installer,
            runtime_probe=self._runtime_probe,
        )
        if self._asset_service is not None and not self._asset_service.is_active(
            self._bundle.bundle_id,
            self._bundle.source_revision,
        ):
            return replace(
                capability,
                available=False,
                degradation_reasons=(
                    *capability.degradation_reasons,
                    "model_not_enabled",
                ),
            )
        return capability

    def create_install_job(self, *, idempotency_key: str) -> JobRecord:
        if self._asset_service is not None:
            return self._asset_service.create_install_job(
                bundle_id=self._bundle.bundle_id,
                idempotency_key=idempotency_key,
            )
        key = _validated_idempotency_key(idempotency_key)
        payload = {
            "bundle_id": self._bundle.bundle_id,
            "version": self._bundle.version,
            "source_revision": self._bundle.source_revision,
        }
        job = self._jobs.create(
            kind=OCR_MODEL_INSTALL_JOB_KIND,
            payload=payload,
            idempotency_key=key,
        )
        if job.kind != OCR_MODEL_INSTALL_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different OCR model command")
        return job

    async def install_models(self, job_id: str) -> None:
        if self._asset_service is not None:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current.kind != OCR_MODEL_INSTALL_JOB_KIND:
                await self._asset_service.install(job_id)
                return
        job = self._require_job(job_id, kind=OCR_MODEL_INSTALL_JOB_KIND)
        self._validate_install_payload(job.payload)
        if job.status in {"succeeded", "cancelled"}:
            return
        checkpoint: dict[str, Any] = {
            "bundle_id": self._bundle.bundle_id,
            "received_bytes": 0,
            "total_bytes": self._bundle.total_size,
        }
        if job.cancel_requested:
            self._finish_cancelled(
                job.id,
                prefix="reader.ocr_models",
                progress=job.progress,
                checkpoint=checkpoint,
                message="The OCR model installation was cancelled",
            )
            return
        self._jobs.update_state(
            job.id,
            status="running",
            progress=0,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.ocr_models.started",
            progress=0,
            payload={
                "bundle_id": self._bundle.bundle_id,
                "total_bytes": self._bundle.total_size,
            },
        )
        current_progress = 0

        def cancelled() -> bool:
            current = self._jobs.get(job.id)
            return current is None or current.cancel_requested

        def report_progress(received: int, total: int) -> None:
            nonlocal checkpoint, current_progress
            current_progress = min(90, max(1, int(received * 90 / total)))
            checkpoint = {
                "bundle_id": self._bundle.bundle_id,
                "received_bytes": received,
                "total_bytes": total,
            }
            self._jobs.update_state(
                job.id,
                status="running",
                progress=current_progress,
                checkpoint=checkpoint,
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="reader.ocr_models.progress",
                progress=current_progress,
                payload=dict(checkpoint),
            )

        try:
            await self._installer.install(
                self._bundle,
                cancelled=cancelled,
                progress=report_progress,
            )
            if cancelled():
                raise _cancelled_download_error()
            capability = self.capability()
            if not capability.models_installed:
                code = (
                    "model_hash_mismatch"
                    if "model_hash_mismatch" in capability.degradation_reasons
                    else "models_missing"
                )
                raise OcrModelDownloadError(
                    code,
                    "The OCR model bundle is incomplete after installation",
                    retryable=False,
                )
        except OcrModelDownloadError as error:
            if error.code == "cancelled":
                self._finish_cancelled(
                    job.id,
                    prefix="reader.ocr_models",
                    progress=current_progress,
                    checkpoint=checkpoint,
                    message=str(error),
                )
            else:
                self._finish_failed(
                    job.id,
                    prefix="reader.ocr_models",
                    progress=current_progress,
                    checkpoint=checkpoint,
                    error=error,
                )
            return
        except Exception:
            self._finish_failed(
                job.id,
                prefix="reader.ocr_models",
                progress=current_progress,
                checkpoint=checkpoint,
                error=OcrError(
                    "internal_error",
                    "The OCR model installation failed unexpectedly",
                    retryable=False,
                ),
            )
            return

        result = {
            "bundle_id": self._bundle.bundle_id,
            "version": self._bundle.version,
            "installed_files": len(self._bundle.files),
            "installed_bytes": self._bundle.total_size,
        }
        checkpoint = {
            "bundle_id": self._bundle.bundle_id,
            "received_bytes": self._bundle.total_size,
            "total_bytes": self._bundle.total_size,
        }
        self._jobs.update_state(
            job.id,
            status="succeeded",
            progress=100,
            result=result,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.ocr_models.succeeded",
            progress=100,
            payload=result,
        )

    def create_recognition_job(
        self,
        *,
        work_id: str,
        document_version_id: str,
        idempotency_key: str,
    ) -> JobRecord:
        key = _validated_idempotency_key(idempotency_key)
        self._require_capability()
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            if session.get(LibraryEntryRecord, work.id) is None:
                raise KeyError(work_id)
            document = session.get(DocumentVersionRecord, document_version_id)
            if document is None or document.work_id != work.id:
                raise KeyError(document_version_id)
            self._validate_document(document)
            path = self._resolved_document_path(document)
            if not path.is_file():
                raise OcrError(
                    "document_unavailable",
                    "The local PDF is unavailable for OCR",
                    retryable=True,
                )
            payload = {
                "work_id": work.id,
                "document_version_id": document.id,
                "content_sha256": document.content_sha256,
                "bundle_id": self._bundle.bundle_id,
                "version": self._bundle.version,
                "source_revision": self._bundle.source_revision,
            }
        job = self._jobs.create(
            kind=OCR_RECOGNITION_JOB_KIND,
            payload=payload,
            idempotency_key=key,
        )
        if job.kind != OCR_RECOGNITION_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to a different OCR command")
        if self._asset_service is not None and job.status in {"queued", "running"}:
            self._asset_service.reference_job(
                self._bundle.bundle_id,
                self._bundle.source_revision,
                job.id,
            )
        return job

    async def execute_recognition(self, job_id: str) -> None:
        job = self._require_job(job_id, kind=OCR_RECOGNITION_JOB_KIND)
        payload = self._read_recognition_payload(job.payload)
        if job.status in {"succeeded", "cancelled"}:
            self._release_recognition_asset(job.id)
            return
        checkpoint: dict[str, Any] = {
            "document_version_id": payload["document_version_id"],
            "completed_pages": 0,
            "page_count": None,
        }
        if job.cancel_requested:
            self._finish_cancelled(
                job.id,
                prefix="reader.ocr",
                progress=job.progress,
                checkpoint=checkpoint,
                message="The scanned-page OCR was cancelled",
            )
            return
        self._jobs.update_state(
            job.id,
            status="running",
            progress=0,
            checkpoint=checkpoint,
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="reader.ocr.started",
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
            current_progress = min(90, max(1, int(completed * 90 / max(total, 1))))
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
                event_type="reader.ocr.progress",
                progress=current_progress,
                payload=dict(checkpoint),
            )

        try:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._recognize_document,
                    payload,
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
                raise _recognition_cancelled_error()
        except OcrError as error:
            if error.code == "cancelled":
                self._finish_cancelled(
                    job.id,
                    prefix="reader.ocr",
                    progress=current_progress,
                    checkpoint=checkpoint,
                    message=str(error),
                )
            else:
                self._finish_failed(
                    job.id,
                    prefix="reader.ocr",
                    progress=current_progress,
                    checkpoint=checkpoint,
                    error=error,
                )
            return
        except Exception:
            self._finish_failed(
                job.id,
                prefix="reader.ocr",
                progress=current_progress,
                checkpoint=checkpoint,
                error=OcrError(
                    "internal_error",
                    "The scanned-page OCR failed unexpectedly",
                    retryable=False,
                ),
            )
            return

        response = {
            "work_id": payload["work_id"],
            "document_version_id": result.document_version_id,
            "page_count": result.page_count,
            "line_count": result.line_count,
            "aggregate_confidence": result.aggregate_confidence,
            "engine_version": result.engine_version,
            "model_bundle_id": result.model_bundle_id,
        }
        final_checkpoint = {
            "document_version_id": result.document_version_id,
            "completed_pages": result.page_count,
            "page_count": result.page_count,
            "engine_version": result.engine_version,
            "model_bundle_id": result.model_bundle_id,
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
            event_type="reader.ocr.succeeded",
            progress=100,
            payload=response,
        )
        self._release_recognition_asset(job.id)

    def recognize_scanned_pages(
        self,
        document_version_id: str,
        *,
        cancelled: Callable[[], bool],
        progress: RecognitionProgress = lambda _completed, _total: None,
    ) -> OcrResult:
        with self._sessions() as session:
            document = session.get(DocumentVersionRecord, document_version_id)
            if document is None:
                raise OcrError(
                    "document_unavailable",
                    "The local PDF is unavailable for OCR",
                    retryable=True,
                )
            payload = {
                "work_id": document.work_id,
                "document_version_id": document.id,
                "content_sha256": document.content_sha256,
                "bundle_id": self._bundle.bundle_id,
                "version": self._bundle.version,
            }
        return self._recognize_document(
            payload,
            cancelled=cancelled,
            progress=progress,
        )

    def resumable_install_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=OCR_MODEL_INSTALL_JOB_KIND)

    def resumable_recognition_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=OCR_RECOGNITION_JOB_KIND)

    def _recognize_document(
        self,
        payload: Mapping[str, str],
        *,
        cancelled: Callable[[], bool],
        progress: RecognitionProgress,
    ) -> OcrResult:
        deadline = self._clock() + self._max_seconds
        self._require_capability()
        self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
        snapshot = self._document_snapshot(payload)
        self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
        if len(snapshot.pages) > self._max_scanned_pages:
            raise OcrError(
                "too_many_pages",
                "The document contains too many scanned pages for safe OCR",
                retryable=False,
            )
        if not snapshot.pages:
            return OcrResult(
                document_version_id=snapshot.document_version_id,
                pages=(),
                page_count=0,
                line_count=0,
                aggregate_confidence=0.0,
                engine_version=self._backend.version,
                model_bundle_id=self._bundle.bundle_id,
            )
        if self._document_processor is not None:
            return self._recognize_isolated(
                snapshot,
                cancelled=cancelled,
                progress=progress,
                deadline=deadline,
            )

        try:
            pymupdf: Any = import_module("pymupdf")
            opened: Any = pymupdf.open(snapshot.path)
            self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
        except OcrError:
            raise
        except Exception as error:
            raise OcrError(
                "document_damaged",
                "The local PDF cannot be rendered safely for OCR",
                retryable=False,
            ) from error

        page_results: list[OcrPageResult] = []
        try:
            with opened as pdf:
                if bool(pdf.needs_pass) or bool(getattr(pdf, "is_repaired", False)):
                    raise OcrError(
                        "document_damaged",
                        "The local PDF cannot be rendered safely for OCR",
                        retryable=False,
                    )
                page_count = int(pdf.page_count)
                character_total = 0
                line_total = 0
                for index, page_snapshot in enumerate(snapshot.pages, start=1):
                    self._check_recognition_runtime(
                        cancelled=cancelled,
                        deadline=deadline,
                    )
                    if page_snapshot.page_number > page_count:
                        raise OcrError(
                            "stale_pages",
                            "The extracted page evidence no longer matches the PDF",
                            retryable=True,
                        )
                    page = pdf.load_page(page_snapshot.page_number - 1)
                    self._check_recognition_runtime(
                        cancelled=cancelled,
                        deadline=deadline,
                    )
                    width = float(page.rect.width)
                    height = float(page.rect.height)
                    if not math.isclose(
                        width, page_snapshot.page_width, abs_tol=0.5
                    ) or not math.isclose(height, page_snapshot.page_height, abs_tol=0.5):
                        raise OcrError(
                            "stale_pages",
                            "The extracted page dimensions no longer match the PDF",
                            retryable=True,
                        )
                    projected_pixels = width * height * OCR_RENDER_SCALE * OCR_RENDER_SCALE
                    if (
                        not math.isfinite(projected_pixels)
                        or projected_pixels > self._max_render_pixels
                    ):
                        raise OcrError(
                            "page_too_large",
                            "The scanned page is too large to render safely for OCR",
                            retryable=False,
                        )
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(OCR_RENDER_SCALE, OCR_RENDER_SCALE),
                        alpha=False,
                    )
                    self._check_recognition_runtime(
                        cancelled=cancelled,
                        deadline=deadline,
                    )
                    if (
                        int(pixmap.width) <= 0
                        or int(pixmap.height) <= 0
                        or int(pixmap.width) * int(pixmap.height) > self._max_render_pixels
                    ):
                        raise OcrError(
                            "page_too_large",
                            "The scanned page is too large to render safely for OCR",
                            retryable=False,
                        )
                    image = cast(bytes, pixmap.tobytes("png"))
                    self._check_recognition_runtime(
                        cancelled=cancelled,
                        deadline=deadline,
                    )
                    if len(image) > self._max_image_bytes:
                        raise OcrError(
                            "image_too_large",
                            "The rendered page image exceeds the safe OCR input limit",
                            retryable=False,
                        )
                    raw_lines = self._backend.recognize(image)
                    self._check_recognition_runtime(
                        cancelled=cancelled,
                        deadline=deadline,
                    )
                    if len(raw_lines) > self._max_lines_per_page:
                        raise _ocr_line_limit_error()
                    line_total += len(raw_lines)
                    if line_total > self._max_lines_total:
                        raise _ocr_line_limit_error()
                    page_character_count = sum(len(line.text) for line in raw_lines)
                    if page_character_count > self._max_characters_per_page:
                        raise _ocr_text_limit_error()
                    character_total += page_character_count
                    if character_total > self._max_characters_total:
                        raise _ocr_text_limit_error()
                    page_result = _normalized_page_result(
                        page_snapshot,
                        rendered_width=int(pixmap.width),
                        rendered_height=int(pixmap.height),
                        lines=tuple(raw_lines),
                        max_characters=self._max_characters_per_page,
                    )
                    page_results.append(page_result)
                    _recognition_progress(progress, index, len(snapshot.pages))
                    self._check_recognition_runtime(
                        cancelled=cancelled,
                        deadline=deadline,
                    )
        except OcrError:
            raise
        except Exception as error:
            raise OcrError(
                "document_damaged",
                "The local PDF could not be rendered safely for OCR",
                retryable=False,
            ) from error

        self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
        engine_version = self._backend.version.strip()
        if not engine_version or len(engine_version) > 80:
            raise OcrError(
                "invalid_output",
                "The OCR backend version is invalid",
                retryable=False,
            )
        result_pages = tuple(page_results)
        result = OcrResult(
            document_version_id=snapshot.document_version_id,
            pages=result_pages,
            page_count=len(result_pages),
            line_count=sum(page.line_count for page in result_pages),
            aggregate_confidence=_aggregate_page_confidence(result_pages),
            engine_version=engine_version,
            model_bundle_id=self._bundle.bundle_id,
        )
        self._persist_result(snapshot, result)
        return result

    def _recognize_isolated(
        self,
        snapshot: _DocumentSnapshot,
        *,
        cancelled: Callable[[], bool],
        progress: RecognitionProgress,
        deadline: float,
    ) -> OcrResult:
        assert self._document_processor is not None
        self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise OcrError(
                "time_limit",
                "The document exceeded the safe OCR time limit",
                retryable=False,
            )
        processed = self._document_processor.process(
            document=snapshot.path,
            pages=snapshot.pages,
            max_render_pixels=self._max_render_pixels,
            max_image_bytes=self._max_image_bytes,
            max_lines_per_page=self._max_lines_per_page,
            max_lines_total=self._max_lines_total,
            max_characters_per_page=self._max_characters_per_page,
            max_characters_total=self._max_characters_total,
            timeout_seconds=remaining,
            cancelled=cancelled,
            progress=progress,
        )
        if len(processed.pages) != len(snapshot.pages):
            raise _invalid_output_error()
        page_results: list[OcrPageResult] = []
        character_total = 0
        line_total = 0
        for page_snapshot, page in zip(snapshot.pages, processed.pages, strict=True):
            self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
            if page.page_number != page_snapshot.page_number:
                raise _invalid_output_error()
            if len(page.lines) > self._max_lines_per_page:
                raise _ocr_line_limit_error()
            line_total += len(page.lines)
            if line_total > self._max_lines_total:
                raise _ocr_line_limit_error()
            page_character_count = sum(len(line.text) for line in page.lines)
            if page_character_count > self._max_characters_per_page:
                raise _ocr_text_limit_error()
            character_total += page_character_count
            if character_total > self._max_characters_total:
                raise _ocr_text_limit_error()
            page_results.append(
                _normalized_page_result(
                    page_snapshot,
                    rendered_width=page.rendered_width,
                    rendered_height=page.rendered_height,
                    lines=page.lines,
                    max_characters=self._max_characters_per_page,
                )
            )
        self._check_recognition_runtime(cancelled=cancelled, deadline=deadline)
        engine_version = processed.engine_version.strip()
        if not engine_version or len(engine_version) > 80:
            raise _invalid_output_error()
        pages = tuple(page_results)
        result = OcrResult(
            document_version_id=snapshot.document_version_id,
            pages=pages,
            page_count=len(pages),
            line_count=sum(page.line_count for page in pages),
            aggregate_confidence=_aggregate_page_confidence(pages),
            engine_version=engine_version,
            model_bundle_id=self._bundle.bundle_id,
        )
        self._persist_result(snapshot, result)
        return result

    def _check_recognition_runtime(
        self,
        *,
        cancelled: Callable[[], bool],
        deadline: float,
    ) -> None:
        if _recognition_cancelled(cancelled):
            raise _recognition_cancelled_error()
        if self._clock() > deadline:
            raise OcrError(
                "time_limit",
                "The document exceeded the safe OCR time limit",
                retryable=False,
            )

    def _document_snapshot(
        self,
        payload: Mapping[str, str],
    ) -> _DocumentSnapshot:
        with self._sessions() as session:
            document = session.get(
                DocumentVersionRecord,
                payload["document_version_id"],
            )
            if (
                document is None
                or document.work_id != payload["work_id"]
                or document.content_sha256 != payload["content_sha256"]
            ):
                raise OcrError(
                    "document_unavailable",
                    "The local PDF changed before OCR started",
                    retryable=True,
                )
            if session.get(LibraryEntryRecord, document.work_id) is None:
                raise OcrError(
                    "document_unavailable",
                    "The paper is no longer in the local library",
                    retryable=True,
                )
            self._validate_document(document)
            path = self._resolved_document_path(document)
            rows = tuple(
                session.scalars(
                    select(DocumentPageTextRecord)
                    .where(DocumentPageTextRecord.document_version_id == document.id)
                    .order_by(DocumentPageTextRecord.page_number)
                )
            )
            pages = scanned_page_snapshots(rows)
        if not path.is_file():
            raise OcrError(
                "document_unavailable",
                "The local PDF file is missing",
                retryable=True,
            )
        return _DocumentSnapshot(
            work_id=payload["work_id"],
            document_version_id=payload["document_version_id"],
            content_sha256=payload["content_sha256"],
            path=path,
            pages=pages,
        )

    def _persist_result(
        self,
        snapshot: _DocumentSnapshot,
        result: OcrResult,
    ) -> None:
        with self._sessions() as session:
            document = session.get(DocumentVersionRecord, snapshot.document_version_id)
            if (
                document is None
                or document.work_id != snapshot.work_id
                or document.content_sha256 != snapshot.content_sha256
                or document.access_status not in LEGAL_DOCUMENT_ACCESS
                or document.media_type.casefold() != "application/pdf"
                or document.extraction_status != "complete"
                or session.get(LibraryEntryRecord, snapshot.work_id) is None
            ):
                raise OcrError(
                    "stale_pages",
                    "The document changed before OCR evidence could be saved",
                    retryable=True,
                )
            rows = tuple(
                session.scalars(
                    select(DocumentPageTextRecord)
                    .where(DocumentPageTextRecord.document_version_id == document.id)
                    .order_by(DocumentPageTextRecord.page_number)
                )
            )
            scanned_rows = select_scanned_page_rows(rows)
            if scanned_page_snapshots(scanned_rows) != snapshot.pages:
                raise OcrError(
                    "stale_pages",
                    "The native page evidence changed before OCR could be saved",
                    retryable=True,
                )
            if tuple(row.page_number for row in scanned_rows) != tuple(
                page.page_number for page in result.pages
            ):
                raise OcrError(
                    "invalid_output",
                    "The OCR result page order is inconsistent",
                    retryable=False,
                )
            observed_at = utc_now()
            for row, page in zip(scanned_rows, result.pages, strict=True):
                row.ocr_state = "complete"
                row.ocr_text = page.text
                row.ocr_blocks = [_ocr_block_payload(block) for block in page.blocks]
                row.ocr_confidence = page.confidence
                row.ocr_engine_version = result.engine_version
                row.ocr_model_bundle_id = result.model_bundle_id
                row.updated_at = observed_at
            session.commit()

    def _require_capability(self) -> None:
        capability = self.capability()
        if capability.available:
            return
        if "runtime_unavailable" in capability.degradation_reasons:
            raise OcrError(
                "runtime_unavailable",
                "The optional local OCR runtime is unavailable",
                retryable=True,
            )
        if "model_hash_mismatch" in capability.degradation_reasons:
            raise OcrError(
                "model_hash_mismatch",
                "The installed OCR model failed integrity verification",
                retryable=False,
            )
        raise OcrError(
            "models_missing",
            "The optional OCR models have not been installed",
            retryable=True,
        )

    @staticmethod
    def _validate_document(document: DocumentVersionRecord) -> None:
        if (
            document.access_status not in LEGAL_DOCUMENT_ACCESS
            or document.media_type.casefold() != "application/pdf"
            or document.relative_path is None
            or document.extraction_status != "complete"
        ):
            raise OcrError(
                "document_unavailable",
                "OCR requires a legally available, extracted local PDF",
                retryable=True,
            )

    def _resolved_document_path(self, document: DocumentVersionRecord) -> Path:
        assert document.relative_path is not None
        try:
            return self._store.resolve_object(
                document.content_sha256,
                document.relative_path,
            )
        except UnsafeStoredPathError as error:
            raise OcrError(
                "document_unavailable",
                "The local PDF path is unsafe",
                retryable=False,
            ) from error

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

    def _require_job(self, job_id: str, *, kind: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != kind:
            raise ValueError("job is not the requested OCR command")
        return job

    def _validate_install_payload(self, value: Mapping[str, Any]) -> None:
        expected = {
            "bundle_id": self._bundle.bundle_id,
            "version": self._bundle.version,
            "source_revision": self._bundle.source_revision,
        }
        if dict(value) != expected:
            raise ValueError("OCR model installation payload is invalid")

    def _read_recognition_payload(self, value: Mapping[str, Any]) -> dict[str, str]:
        keys = (
            "work_id",
            "document_version_id",
            "content_sha256",
            "bundle_id",
            "version",
        )
        if any(not isinstance(value.get(key), str) for key in keys):
            raise ValueError("OCR recognition payload is invalid")
        result = {key: cast(str, value[key]) for key in keys}
        revision = value.get("source_revision")
        if revision is not None and not isinstance(revision, str):
            raise ValueError("OCR recognition payload is invalid")
        result["source_revision"] = (
            revision if isinstance(revision, str) else self._bundle.source_revision
        )
        if (
            result["bundle_id"] != self._bundle.bundle_id
            or result["version"] != self._bundle.version
            or result["source_revision"] != self._bundle.source_revision
        ):
            raise ValueError("OCR recognition payload uses a different model bundle")
        return result

    def _finish_cancelled(
        self,
        job_id: str,
        *,
        prefix: str,
        progress: int,
        checkpoint: dict[str, Any],
        message: str,
    ) -> None:
        result = {"cancelled": True}
        self._jobs.update_state(
            job_id,
            status="cancelled",
            progress=progress,
            result=result,
            checkpoint=checkpoint,
            error_code="cancelled",
            error_message=message,
        )
        self._jobs.append_event(
            job_id=job_id,
            event_type=f"{prefix}.cancelled",
            progress=progress,
            payload=result,
        )
        if prefix == "reader.ocr":
            self._release_recognition_asset(job_id)

    def _finish_failed(
        self,
        job_id: str,
        *,
        prefix: str,
        progress: int,
        checkpoint: dict[str, Any],
        error: OcrError,
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
            event_type=f"{prefix}.failed",
            progress=progress,
            payload={"code": error.code, "retryable": error.retryable},
        )
        if prefix == "reader.ocr":
            self._release_recognition_asset(job_id)

    def _release_recognition_asset(self, job_id: str) -> None:
        if self._asset_service is not None:
            self._asset_service.release_job(
                self._bundle.bundle_id,
                self._bundle.source_revision,
                job_id,
            )


def _validated_idempotency_key(value: str) -> str:
    key = value.strip()
    if not key or len(key) > 160:
        raise ValueError("idempotency key must contain 1-160 characters")
    return key


def _recognition_cancelled(callback: Callable[[], bool]) -> bool:
    try:
        return bool(callback())
    except Exception as error:
        raise OcrError(
            "internal_error",
            "The OCR cancellation check failed",
            retryable=False,
        ) from error


def _recognition_progress(
    callback: RecognitionProgress,
    completed: int,
    total: int,
) -> None:
    try:
        callback(completed, total)
    except Exception as error:
        raise OcrError(
            "internal_error",
            "The OCR progress callback failed",
            retryable=False,
        ) from error
