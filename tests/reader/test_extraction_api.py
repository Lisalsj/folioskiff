from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths
from tests.reader.pdf_fixtures import write_text_pdf

NOW = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)


def reader_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app

    return create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def bootstrap(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
        "Idempotency-Key": "extract-reader-paper",
    }


def add_local_document(app: Any, pdf_path: Path, label: str) -> tuple[str, str]:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    payload = pdf_path.read_bytes()
    stored = ContentStore(app.state.paths.library_root).ingest(
        (payload,),
        media_type="application/pdf",
        max_bytes=10 * 1024 * 1024,
    )
    ingested = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="fixture",
            external_id=f"extraction-api-{label}",
            title=f"Extraction API {label}",
            authors=("Alice",),
        )
    )
    with app.state.sessions() as session:
        document = DocumentVersionRecord(
            work_id=ingested.work.id,
            source_record_id=ingested.source_record.id,
            content_sha256=stored.content_sha256,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=NOW,
            extraction_status="pending",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename=f"{label}.pdf",
            acquisition_kind="manual_import",
            last_accessed_at=NOW,
            created_at=NOW,
        )
        session.add(document)
        session.add(
            LibraryEntryRecord(
                work_id=ingested.work.id,
                added_via="pdf",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
        return ingested.work.id, document.id


class StaticExtractor:
    def extract(self, document: Path, *, cancelled, progress):  # type: ignore[no-untyped-def]
        from paper_reader.reader.extraction import (
            ExtractionResult,
            PageText,
            TextBlock,
        )

        assert document.is_file()
        assert cancelled() is False
        progress(1, 1)
        block = TextBlock(
            x0=12,
            y0=24,
            x1=180,
            y1=42,
            text="Static page evidence\n",
            block_number=0,
        )
        page = PageText(
            page_number=1,
            page_width=420,
            page_height=595,
            text="Static page evidence\n",
            blocks=(block,),
            native_character_count=18,
            likely_scanned=False,
            ocr_state="not_needed",
        )
        return ExtractionResult(
            pages=(page,),
            page_count=1,
            native_character_count=18,
            scanned_page_count=0,
            extractor_version="fixture-v1",
        )


def install_static_service(app: Any) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService
    from paper_reader.reader.storage import ContentStore

    app.state.document_extraction_service = DocumentExtractionService(
        sessions=app.state.sessions,
        jobs=JobRepository(app.state.sessions),
        store=ContentStore(app.state.paths.library_root),
        extractor=StaticExtractor(),
    )


async def await_terminal_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("extraction job did not reach a terminal state")


@pytest.mark.asyncio
async def test_extraction_api_runs_in_background_reuses_command_and_lists_pages(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)
    pdf = write_text_pdf(tmp_path / "api.pdf", ("API source",))
    work_id, document_id = add_local_document(app, pdf, "success")

    async with local_client(app) as client:
        install_static_service(app)
        headers = await bootstrap(client)
        queued = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/extract",
            headers=headers,
        )
        assert queued.status_code == 202
        finished = await await_terminal_job(client, queued.json()["id"])
        pages = await client.get(
            f"/api/v1/works/{work_id}/documents/{document_id}/pages"
        )
        duplicate = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/extract",
            headers=headers,
        )

    assert queued.json()["kind"] == "reader.extract_document"
    assert finished["status"] == "succeeded"
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == queued.json()["id"]
    assert pages.status_code == 200
    assert len(pages.json()) == 1
    page = pages.json()[0]
    assert page["page_number"] == 1
    assert page["text"] == "Static page evidence\n"
    assert page["text_source"] == "native"
    assert page["native_text"] == "Static page evidence\n"
    assert page["ocr_text"] is None
    assert page["blocks"][0]["x0"] == 12
    assert "relative_path" not in pages.text


@pytest.mark.asyncio
async def test_extraction_api_enforces_session_csrf_key_and_document_membership(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)
    pdf = write_text_pdf(tmp_path / "validation.pdf", ("Validation",))
    work_id, document_id = add_local_document(app, pdf, "validation")

    async with local_client(app) as client:
        install_static_service(app)
        unauthorized = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/extract"
        )
        headers = await bootstrap(client)
        no_csrf = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/extract",
            headers={
                "Origin": "http://127.0.0.1",
                "Idempotency-Key": "no-csrf",
            },
        )
        missing_key = await client.post(
            f"/api/v1/library/works/{work_id}/documents/{document_id}/extract",
            headers={
                "Origin": headers["Origin"],
                "X-CSRF-Token": headers["X-CSRF-Token"],
            },
        )
        wrong_work = await client.post(
            f"/api/v1/library/works/missing/documents/{document_id}/extract",
            headers={**headers, "Idempotency-Key": "wrong-work"},
        )
        missing_document = await client.post(
            f"/api/v1/library/works/{work_id}/documents/missing/extract",
            headers={**headers, "Idempotency-Key": "missing-document"},
        )
        pages_missing = await client.get(
            f"/api/v1/works/{work_id}/documents/missing/pages"
        )

    assert unauthorized.status_code == 401
    assert no_csrf.status_code == 403
    assert missing_key.status_code == 422
    assert wrong_work.status_code == 404
    assert missing_document.status_code == 404
    assert pages_missing.status_code == 404


@pytest.mark.asyncio
async def test_app_startup_resumes_queued_extraction_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import (
        DocumentExtractionService,
        PdfTextExtractor,
    )
    from paper_reader.reader.storage import ContentStore

    app = reader_app(tmp_path)
    pdf = write_text_pdf(tmp_path / "resume.pdf", ("Resume",))
    work_id, document_id = add_local_document(app, pdf, "resume")
    service = DocumentExtractionService(
        sessions=app.state.sessions,
        jobs=JobRepository(app.state.sessions),
        store=ContentStore(app.state.paths.library_root),
        extractor=PdfTextExtractor(),
    )
    queued = service.create_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="resume-extraction",
    )
    resumed: list[str] = []

    async def record_resume(self, job_id: str) -> None:  # type: ignore[no-untyped-def]
        resumed.append(job_id)

    monkeypatch.setattr(DocumentExtractionService, "execute", record_resume)

    async with local_client(app):
        for _ in range(100):
            if resumed:
                break
            await asyncio.sleep(0.01)

    assert resumed == [queued.id]
