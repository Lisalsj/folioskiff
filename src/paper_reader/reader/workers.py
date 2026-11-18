"""In-process worker entry points for durable reader jobs."""

from __future__ import annotations

from paper_reader.reader.acquisition import DocumentAcquisitionService
from paper_reader.reader.extraction import DocumentExtractionService
from paper_reader.reader.ocr_service import OcrService


async def resume_document_acquisition(
    service: DocumentAcquisitionService,
    job_id: str,
) -> None:
    await service.execute(job_id)


async def resume_document_extraction(
    service: DocumentExtractionService,
    job_id: str,
) -> None:
    await service.execute(job_id)


async def resume_ocr_model_installation(
    service: OcrService,
    job_id: str,
) -> None:
    await service.install_models(job_id)


async def resume_ocr_recognition(
    service: OcrService,
    job_id: str,
) -> None:
    await service.execute_recognition(job_id)
