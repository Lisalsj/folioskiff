from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths
from tests.reader.pdf_fixtures import write_text_pdf


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


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    assert response.status_code == 204


def add_local_document(
    app: Any,
    pdf_path: Path,
    *,
    label: str,
    access_status: str = "user_provided",
) -> tuple[str, str, bytes]:
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
            external_id=f"content-api-{label}",
            title=f"Content API {label}",
            authors=("Alice Chen",),
        )
    )
    with app.state.sessions() as session:
        document = DocumentVersionRecord(
            work_id=ingested.work.id,
            source_record_id=ingested.source_record.id,
            content_sha256=stored.content_sha256,
            access_status=access_status,
            media_type="application/pdf",
            completeness=100,
            source_updated_at=datetime(2026, 7, 13, tzinfo=UTC),
            extraction_status="pending",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename="研究 阅读器.pdf",
            acquisition_kind="manual_import",
            last_accessed_at=None,
        )
        session.add(document)
        session.add(
            LibraryEntryRecord(
                work_id=ingested.work.id,
                added_via="pdf",
            )
        )
        session.commit()
        return ingested.work.id, document.id, payload


@pytest.mark.asyncio
async def test_document_content_requires_session_and_streams_inline_pdf_ranges(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord

    app = reader_app(tmp_path)
    pdf_path = write_text_pdf(tmp_path / "reader.pdf", ("Paper reader content",))
    _, document_id, payload = add_local_document(
        app,
        pdf_path,
        label="stream",
    )

    async with local_client(app) as client:
        unauthorized = await client.get(f"/api/v1/documents/{document_id}/content")
        await bootstrap(client)
        complete = await client.get(f"/api/v1/documents/{document_id}/content")
        partial = await client.get(
            f"/api/v1/documents/{document_id}/content",
            headers={"Range": "bytes=0-15"},
        )

    assert unauthorized.status_code == 401
    assert complete.status_code == 200
    assert complete.content == payload
    assert complete.headers["content-type"] == "application/pdf"
    assert complete.headers["accept-ranges"] == "bytes"
    assert complete.headers["cache-control"] == "private, no-store"
    assert complete.headers["x-content-type-options"] == "nosniff"
    assert complete.headers["content-disposition"].startswith("inline;")
    assert "filename*=utf-8''" in complete.headers["content-disposition"].casefold()
    assert partial.status_code == 206
    assert partial.content == payload[:16]
    assert partial.headers["content-range"] == f"bytes 0-15/{len(payload)}"
    with app.state.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        assert document is not None
        assert document.last_accessed_at is not None


@pytest.mark.asyncio
async def test_document_content_distinguishes_missing_from_unavailable_without_paths(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import LibraryEntryRecord

    app = reader_app(tmp_path)
    pdf_path = write_text_pdf(tmp_path / "unavailable.pdf", ("Unavailable",))
    _, restricted_id, _ = add_local_document(
        app,
        pdf_path,
        label="restricted",
        access_status="restricted",
    )
    _, missing_file_id, _ = add_local_document(
        app,
        pdf_path,
        label="missing-file",
    )
    unsaved_work_id, unsaved_id, _ = add_local_document(
        app,
        pdf_path,
        label="not-in-library",
    )
    with app.state.sessions() as session:
        missing_file = session.get(DocumentVersionRecord, missing_file_id)
        assert missing_file is not None
        missing_file.relative_path = None
        unsaved_entry = session.get(LibraryEntryRecord, unsaved_work_id)
        assert unsaved_entry is not None
        session.delete(unsaved_entry)
        session.commit()

    async with local_client(app) as client:
        await bootstrap(client)
        missing = await client.get("/api/v1/documents/not-found/content")
        restricted = await client.get(
            f"/api/v1/documents/{restricted_id}/content"
        )
        unavailable = await client.get(
            f"/api/v1/documents/{missing_file_id}/content"
        )
        unsaved = await client.get(f"/api/v1/documents/{unsaved_id}/content")

    assert missing.status_code == 404
    assert restricted.status_code == 410
    assert unavailable.status_code == 410
    assert unsaved.status_code == 404
    for response in (missing, restricted, unavailable, unsaved):
        assert str(app.state.paths.library_root) not in response.text
        assert "relative_path" not in response.text


@pytest.mark.asyncio
async def test_document_content_rejects_tampered_paths_and_missing_bytes(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord

    app = reader_app(tmp_path)
    pdf_path = write_text_pdf(tmp_path / "tampered.pdf", ("Tampered",))
    _, tampered_id, _ = add_local_document(app, pdf_path, label="tampered")
    _, deleted_id, _ = add_local_document(app, pdf_path, label="deleted")
    _, size_mismatch_id, _ = add_local_document(
        app, pdf_path, label="size-mismatch"
    )
    with app.state.sessions() as session:
        tampered = session.get(DocumentVersionRecord, tampered_id)
        deleted = session.get(DocumentVersionRecord, deleted_id)
        size_mismatch = session.get(DocumentVersionRecord, size_mismatch_id)
        assert (
            tampered is not None
            and deleted is not None
            and size_mismatch is not None
        )
        tampered.relative_path = "../outside.pdf"
        size_mismatch.byte_size += 1
        deleted_path = app.state.paths.library_root / deleted.relative_path
        session.commit()
    deleted_path.unlink()

    async with local_client(app) as client:
        await bootstrap(client)
        tampered_response = await client.get(
            f"/api/v1/documents/{tampered_id}/content"
        )
        deleted_response = await client.get(
            f"/api/v1/documents/{deleted_id}/content"
        )
        size_mismatch_response = await client.get(
            f"/api/v1/documents/{size_mismatch_id}/content"
        )

    assert tampered_response.status_code == 410
    assert deleted_response.status_code == 410
    assert size_mismatch_response.status_code == 410
    assert "outside.pdf" not in tampered_response.text
    assert str(deleted_path) not in deleted_response.text


def test_safe_pdf_filename_preserves_extension_after_sanitizing_and_truncating() -> None:
    from paper_reader.reader.content import _safe_pdf_filename

    filename = _safe_pdf_filename(f"../{'research-' * 40}\x00notes")

    assert len(filename) <= 240
    assert filename.endswith(".pdf")
    assert "/" not in filename
    assert "\\" not in filename
    assert "\x00" not in filename


@pytest.mark.asyncio
async def test_document_content_remains_available_after_work_identity_merge(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    app = reader_app(tmp_path)
    identity = IdentityRepository(app.state.sessions)
    target = identity.ingest(
        SourcePaper(
            connector_id="openreview",
            external_id="content-merge-target",
            title="Tool-Augmented Agents for Scientific Discovery",
            authors=("A. Chen", "R. Kumar"),
        )
    )
    candidate = identity.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.76543v1",
            title="Tool Augmented Agent for Scientific Discoveries",
            authors=("Alice Chen", "Ravi Kumar"),
        )
    )
    assert candidate.merge_review is not None
    payload = write_text_pdf(tmp_path / "merged.pdf", ("Merged identity",)).read_bytes()
    stored = ContentStore(app.state.paths.library_root).ingest(
        (payload,), media_type="application/pdf", max_bytes=10 * 1024 * 1024
    )
    with app.state.sessions() as session:
        document = DocumentVersionRecord(
            work_id=candidate.work.id,
            source_record_id=candidate.source_record.id,
            content_sha256=stored.content_sha256,
            access_status="open",
            media_type="application/pdf",
            completeness=100,
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename="merged.pdf",
            acquisition_kind="download",
        )
        session.add(document)
        session.add(
            LibraryEntryRecord(work_id=candidate.work.id, added_via="pdf")
        )
        session.commit()
        document_id = document.id
    identity.decide_merge_review(candidate.merge_review.id, decision="merge")

    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get(f"/api/v1/documents/{document_id}/content")

    assert response.status_code == 200
    assert response.content == payload
    with app.state.sessions() as session:
        moved = session.get(DocumentVersionRecord, document_id)
        assert moved is not None
        assert moved.work_id == target.work.id
