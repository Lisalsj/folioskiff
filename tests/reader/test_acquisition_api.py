from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


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
        "Idempotency-Key": "acquire-reader-paper",
    }


class ApiPdfDownloader:
    def __init__(self, store) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.calls: list[str] = []

    async def download(self, url, *, cancelled, progress):  # type: ignore[no-untyped-def]
        self.calls.append(url)
        progress(len(PDF_BYTES), len(PDF_BYTES))
        return self.store.ingest(
            (PDF_BYTES,),
            media_type="application/pdf",
            max_bytes=1024,
            cancelled=cancelled,
        )


def install_fake_acquisition_service(app):  # type: ignore[no-untyped-def]
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.acquisition import DocumentAcquisitionService
    from paper_reader.reader.storage import ContentStore

    downloader = ApiPdfDownloader(ContentStore(app.state.paths.library_root))
    app.state.document_acquisition_service = DocumentAcquisitionService(
        sessions=app.state.sessions,
        jobs=JobRepository(app.state.sessions),
        downloader=downloader,
    )
    return downloader


async def await_terminal_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("acquisition job did not reach a terminal state")


@pytest.mark.asyncio
async def test_acquisition_api_returns_202_runs_in_background_and_reuses_command(
    tmp_path: Path,
) -> None:
    from sqlalchemy import select

    from paper_reader.discovery.models import SourceRecord

    app = reader_app(tmp_path)
    async with local_client(app) as client:
        downloader = install_fake_acquisition_service(app)
        headers = await bootstrap(client)
        imported = await client.post(
            "/api/v1/library/imports/reference",
            json={
                "kind": "doi",
                "value": "10.1000/acquisition-api",
                "title": "Acquisition API Paper",
                "authors": ["Alice"],
            },
            headers=headers,
        )
        work_id = imported.json()["work_id"]
        with app.state.sessions() as session:
            source_id = session.scalar(
                select(SourceRecord.id).where(SourceRecord.work_id == work_id)
            )
        payload = {
            "source_record_id": source_id,
            "url": "https://example.org/open-paper.pdf#page=3",
            "access_status": "open",
            "reason": "save",
        }

        queued = await client.post(
            f"/api/v1/library/works/{work_id}/documents/acquire",
            json=payload,
            headers=headers,
        )
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        finished = await await_terminal_job(client, job_id)
        documents = await client.get(f"/api/v1/works/{work_id}/documents")
        duplicate = await client.post(
            f"/api/v1/library/works/{work_id}/documents/acquire",
            json=payload,
            headers=headers,
        )

    assert queued.json()["kind"] == "reader.acquire_document"
    assert finished["status"] == "succeeded"
    assert finished["result"]["work_id"] == work_id
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == job_id
    assert downloader.calls == ["https://example.org/open-paper.pdf"]
    assert documents.status_code == 200
    assert len(documents.json()) == 1
    assert documents.json()[0]["acquisition_kind"] == "download"
    assert "relative_path" not in documents.text


@pytest.mark.asyncio
async def test_acquisition_api_enforces_session_csrf_idempotency_and_open_access(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)
    async with local_client(app) as client:
        unauthorized = await client.post(
            "/api/v1/library/works/missing/documents/acquire",
            json={
                "url": "https://example.org/paper.pdf",
                "access_status": "open",
                "reason": "open",
            },
        )
        install_fake_acquisition_service(app)
        headers = await bootstrap(client)
        imported = await client.post(
            "/api/v1/library/imports/reference",
            json={
                "kind": "url",
                "value": "https://example.org/paper",
                "title": "Validation Paper",
            },
            headers=headers,
        )
        work_id = imported.json()["work_id"]
        payload = {
            "url": "https://example.org/paper.pdf",
            "access_status": "open",
            "reason": "open",
        }
        no_csrf = await client.post(
            f"/api/v1/library/works/{work_id}/documents/acquire",
            json=payload,
            headers={
                "Origin": "http://127.0.0.1",
                "Idempotency-Key": "no-csrf",
            },
        )
        missing_key = await client.post(
            f"/api/v1/library/works/{work_id}/documents/acquire",
            json=payload,
            headers={
                "Origin": headers["Origin"],
                "X-CSRF-Token": headers["X-CSRF-Token"],
            },
        )
        restricted = await client.post(
            f"/api/v1/library/works/{work_id}/documents/acquire",
            json={**payload, "access_status": "restricted"},
            headers={**headers, "Idempotency-Key": "restricted"},
        )
        unsafe = await client.post(
            f"/api/v1/library/works/{work_id}/documents/acquire",
            json={**payload, "url": "http://127.0.0.1/private.pdf"},
            headers={**headers, "Idempotency-Key": "unsafe"},
        )
        missing_work = await client.post(
            "/api/v1/library/works/missing/documents/acquire",
            json=payload,
            headers={**headers, "Idempotency-Key": "missing"},
        )

    assert unauthorized.status_code == 401
    assert no_csrf.status_code == 403
    assert missing_key.status_code == 422
    assert restricted.status_code == 422
    assert unsafe.status_code == 422
    assert missing_work.status_code == 404


@pytest.mark.asyncio
async def test_app_startup_resumes_queued_acquisition_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.acquisition import (
        AcquisitionReason,
        AcquisitionRequest,
        DocumentAcquisitionService,
    )
    from paper_reader.reader.storage import ContentStore

    app = reader_app(tmp_path)
    jobs = JobRepository(app.state.sessions)
    work = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.99999",
            title="Restart Recovery",
            authors=("Alice",),
        )
    ).work
    queued = DocumentAcquisitionService(
        sessions=app.state.sessions,
        jobs=jobs,
        downloader=ApiPdfDownloader(ContentStore(app.state.paths.library_root)),
    ).create_job(
        AcquisitionRequest(
            work_id=work.id,
            source_record_id=None,
            url="https://example.org/restart.pdf",
            access_status="open",
            reason=AcquisitionReason.OPEN,
        ),
        idempotency_key="restart-job",
    )
    resumed: list[str] = []

    async def record_resume(self, job_id: str) -> None:  # type: ignore[no-untyped-def]
        resumed.append(job_id)

    monkeypatch.setattr(DocumentAcquisitionService, "execute", record_resume)

    async with local_client(app):
        for _ in range(100):
            if resumed:
                break
            await asyncio.sleep(0.01)

    assert resumed == [queued.id]
