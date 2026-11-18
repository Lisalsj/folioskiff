from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from paper_reader.jobs.models import JobRecord
    from paper_reader.reader import OcrCapability, OcrModelRole

from tests.reader.test_extraction_api import (
    add_local_document,
    await_terminal_job,
    bootstrap,
    local_client,
    reader_app,
)

NOW = datetime(2026, 7, 13, 11, 0, tzinfo=UTC)


def ready_capability() -> OcrCapability:
    from paper_reader.reader import OcrCapability, OcrModelFileStatus

    roles: tuple[OcrModelRole, ...] = ("det", "cls", "rec")
    files = tuple(
        OcrModelFileStatus(
            role=role,
            filename=f"{role}.onnx",
            size=10,
            sha256=role * 21 + role[0],
            installed=True,
            integrity="verified",
        )
        for role in roles
    )
    return OcrCapability(
        available=True,
        runtime_available=True,
        models_installed=True,
        bundle_id="fixture-ocr",
        display_name="Fixture OCR",
        version="fixture-v1",
        total_size=30,
        license_name="Apache-2.0",
        license_url="https://example.test/license",
        copyright_notice="Fixture copyright",
        source_url="https://example.test/source",
        source_revision="f" * 40,
        files=files,
        degradation_reasons=(),
    )


class StubOcrService:
    def __init__(self, jobs: Any) -> None:
        self.jobs = jobs

    def capability(self) -> OcrCapability:
        return ready_capability()

    def create_install_job(self, *, idempotency_key: str) -> JobRecord:
        payload = {"bundle_id": "fixture-ocr"}
        job = self.jobs.create(
            kind="reader.install_ocr_models",
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if job.kind != "reader.install_ocr_models" or job.payload != payload:
            raise ValueError("idempotency key belongs to a different OCR model command")
        return cast("JobRecord", job)

    async def install_models(self, job_id: str) -> None:
        self.jobs.update_state(
            job_id,
            status="succeeded",
            progress=100,
            result={"bundle_id": "fixture-ocr", "installed_files": 3},
        )

    def create_recognition_job(
        self,
        *,
        work_id: str,
        document_version_id: str,
        idempotency_key: str,
    ) -> JobRecord:
        payload = {
            "work_id": work_id,
            "document_version_id": document_version_id,
        }
        job = self.jobs.create(
            kind="reader.ocr_document",
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if job.kind != "reader.ocr_document" or job.payload != payload:
            raise ValueError("idempotency key belongs to a different OCR command")
        return cast("JobRecord", job)

    async def execute_recognition(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        assert job is not None
        self.jobs.update_state(
            job_id,
            status="succeeded",
            progress=100,
            result={
                "document_version_id": job.payload["document_version_id"],
                "page_count": 1,
                "line_count": 2,
            },
        )


@pytest.mark.asyncio
async def test_ocr_api_exposes_capability_and_runs_explicit_jobs(
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from tests.reader.pdf_fixtures import write_text_pdf

    app = reader_app(tmp_path)
    pdf = write_text_pdf(tmp_path / "ocr-api.pdf", ("OCR API",))
    work_id, document_id = add_local_document(app, pdf, "ocr-api")

    async with local_client(app) as client:
        app.state.ocr_service = StubOcrService(JobRepository(app.state.sessions))
        headers = await bootstrap(client)
        capability = await client.get("/api/v1/settings/ocr")
        install = await client.post(
            "/api/v1/settings/ocr/models/install",
            headers={**headers, "Idempotency-Key": "ocr-model-install"},
        )
        installed = await await_terminal_job(client, install.json()["id"])
        recognition = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/ocr",
            headers={**headers, "Idempotency-Key": "ocr-document"},
        )
        recognized = await await_terminal_job(client, recognition.json()["id"])
        duplicate = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/ocr",
            headers={**headers, "Idempotency-Key": "ocr-document"},
        )

    assert capability.status_code == 200
    body = capability.json()
    assert body["available"] is True
    assert body["bundle_id"] == "fixture-ocr"
    assert body["total_size"] == 30
    assert body["license_name"] == "Apache-2.0"
    assert body["files"][0]["integrity"] == "verified"
    assert "models_root" not in capability.text
    assert install.status_code == 202
    assert installed["status"] == "succeeded"
    assert recognition.status_code == 202
    assert recognized["status"] == "succeeded"
    assert recognized["result"]["line_count"] == 2
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == recognition.json()["id"]


@pytest.mark.asyncio
async def test_ocr_api_enforces_session_csrf_keys_and_capability_conflicts(
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader import OcrError
    from tests.reader.pdf_fixtures import write_text_pdf

    class UnavailableOcrService(StubOcrService):
        def create_recognition_job(self, **_: object):  # type: ignore[no-untyped-def]
            raise OcrError(
                "models_missing",
                "The optional OCR models have not been installed",
                retryable=True,
            )

    app = reader_app(tmp_path)
    pdf = write_text_pdf(tmp_path / "ocr-security.pdf", ("OCR Security",))
    work_id, document_id = add_local_document(app, pdf, "ocr-security")

    async with local_client(app) as client:
        app.state.ocr_service = UnavailableOcrService(JobRepository(app.state.sessions))
        unauthorized_capability = await client.get("/api/v1/settings/ocr")
        unauthorized_install = await client.post(
            "/api/v1/settings/ocr/models/install",
            headers={"Idempotency-Key": "unauthorized"},
        )
        headers = await bootstrap(client)
        no_csrf = await client.post(
            "/api/v1/settings/ocr/models/install",
            headers={
                "Origin": headers["Origin"],
                "Idempotency-Key": "no-csrf",
            },
        )
        missing_key = await client.post(
            "/api/v1/settings/ocr/models/install",
            headers={
                "Origin": headers["Origin"],
                "X-CSRF-Token": headers["X-CSRF-Token"],
            },
        )
        unavailable = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/ocr",
            headers={**headers, "Idempotency-Key": "models-missing"},
        )

    assert unauthorized_capability.status_code == 401
    assert unauthorized_install.status_code == 401
    assert no_csrf.status_code == 403
    assert missing_key.status_code == 422
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"] == {
        "code": "models_missing",
        "message": "The optional OCR models have not been installed",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_page_evidence_api_exposes_ocr_confidence_and_provenance(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_text_pdf

    app = reader_app(tmp_path)
    pdf = write_text_pdf(tmp_path / "ocr-evidence.pdf", ("Native fallback",))
    work_id, document_id = add_local_document(app, pdf, "ocr-evidence")
    with app.state.sessions() as session:
        session.add(
            DocumentPageTextRecord(
                document_version_id=document_id,
                page_number=1,
                page_width=420,
                page_height=595,
                native_text="",
                native_blocks=[],
                native_character_count=0,
                likely_scanned=True,
                ocr_state="complete",
                ocr_text="癌症 diffusion\n",
                ocr_blocks=[
                    {
                        "x0": 10.0,
                        "y0": 20.0,
                        "x1": 200.0,
                        "y1": 45.0,
                        "text": "癌症 diffusion",
                        "block_number": 0,
                        "confidence": 0.94,
                    }
                ],
                ocr_confidence=0.94,
                ocr_engine_version="rapidocr-3.8.1",
                ocr_model_bundle_id="rapidocr-ppocrv5-ch-en-mobile",
                extractor_version="pymupdf-test",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()

    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get(
            f"/api/v1/works/{work_id}/documents/{document_id}/pages"
        )

    assert response.status_code == 200
    page = response.json()[0]
    assert page["text_source"] == "ocr"
    assert page["text"] == "癌症 diffusion\n"
    assert page["blocks"][0]["confidence"] == 0.94
    assert page["ocr_confidence"] == 0.94
    assert page["ocr_engine_version"] == "rapidocr-3.8.1"
    assert page["ocr_model_bundle_id"] == "rapidocr-ppocrv5-ch-en-mobile"
    assert "relative_path" not in response.text


@pytest.mark.asyncio
async def test_app_startup_resumes_both_ocr_job_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.ocr_contracts import (
        OCR_MODEL_INSTALL_JOB_KIND,
        OCR_RECOGNITION_JOB_KIND,
    )
    from paper_reader.reader.ocr_service import OcrService

    app = reader_app(tmp_path)
    jobs = JobRepository(app.state.sessions)
    install = jobs.create(
        kind=OCR_MODEL_INSTALL_JOB_KIND,
        payload={},
        idempotency_key="resume-ocr-models",
    )
    recognition = jobs.create(
        kind=OCR_RECOGNITION_JOB_KIND,
        payload={},
        idempotency_key="resume-ocr-document",
    )
    resumed: list[tuple[str, str]] = []

    async def record_install(self, job_id: str) -> None:  # type: ignore[no-untyped-def]
        resumed.append(("install", job_id))

    async def record_recognition(self, job_id: str) -> None:  # type: ignore[no-untyped-def]
        resumed.append(("recognition", job_id))

    monkeypatch.setattr(OcrService, "install_models", record_install)
    monkeypatch.setattr(OcrService, "execute_recognition", record_recognition)

    async with local_client(app):
        for _ in range(100):
            if len(resumed) == 2:
                break
            await asyncio.sleep(0.01)

    assert sorted(resumed) == sorted(
        (("install", install.id), ("recognition", recognition.id))
    )


@pytest.mark.asyncio
async def test_app_startup_and_capability_probe_do_not_import_ocr_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.reader import ocr_runtime

    imported: list[str] = []

    def reject_runtime_import(name: str) -> Any:
        imported.append(name)
        raise AssertionError("OCR runtime import must remain lazy")

    monkeypatch.setattr(ocr_runtime, "import_module", reject_runtime_import)
    app = reader_app(tmp_path)

    async with local_client(app) as client:
        await bootstrap(client)
        capability = await client.get("/api/v1/settings/ocr")

    assert capability.status_code == 200
    assert imported == []


@pytest.mark.asyncio
@pytest.mark.resource_serial
async def test_native_extraction_marks_scanned_pages_without_starting_ocr(
    tmp_path: Path,
) -> None:
    from tests.reader.pdf_fixtures import write_image_only_pdf

    app = reader_app(tmp_path)
    pdf = write_image_only_pdf(tmp_path / "scan-only.pdf")
    work_id, document_id = add_local_document(app, pdf, "scan-only")

    async with local_client(app) as client:
        headers = await bootstrap(client)
        extraction = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/extract",
            headers={**headers, "Idempotency-Key": "extract-scan-only"},
        )
        finished = await await_terminal_job(client, extraction.json()["id"])
        pages = await client.get(
            f"/api/v1/works/{work_id}/documents/{document_id}/pages"
        )
        jobs = await client.get("/api/v1/jobs")

    assert extraction.status_code == 202
    assert finished["status"] == "succeeded"
    assert pages.status_code == 200
    assert pages.json()[0]["likely_scanned"] is True
    assert pages.json()[0]["ocr_state"] == "pending"
    assert pages.json()[0]["ocr_text"] is None
    assert {job["kind"] for job in jobs.json()} == {"reader.extract_document"}
