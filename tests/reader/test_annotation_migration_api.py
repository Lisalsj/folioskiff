from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths
from tests.reader.pdf_fixtures import write_text_pdf
from tests.reader.test_annotation_migrations import NOW, _persist_extraction


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
    }


def migration_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app
    from paper_reader.reader.annotations import (
        AnnotationColor,
        AnnotationInput,
        AnnotationKind,
        AnnotationRepository,
        PdfRect,
    )
    from paper_reader.reader.imports import LibraryImportService, PdfImportInput
    from paper_reader.reader.storage import ContentStore

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="secret")
    imports = LibraryImportService(
        sessions=app.state.sessions,
        store=ContentStore(paths.library_root),
        clock=lambda: NOW,
    )
    source_path = write_text_pdf(
        tmp_path / "api-source.pdf",
        ("Before. API migration evidence. After.",),
    )
    target_path = write_text_pdf(
        tmp_path / "api-target.pdf",
        ("Before. API migration evidence. After. Revised.",),
    )
    other_path = write_text_pdf(tmp_path / "api-other.pdf", ("Other Work.",))
    source = imports.import_pdf(
        PdfImportInput(
            work_id=None,
            title="API versioned paper",
            authors=("Alice Chen",),
            doi="10.1000/api-versioned",
            original_filename=source_path.name,
            rights_confirmed=True,
        ),
        (source_path.read_bytes(),),
    )
    target = imports.import_pdf(
        PdfImportInput(
            work_id=source.work_id,
            title="API versioned paper",
            authors=("Alice Chen",),
            doi="10.1000/api-versioned",
            original_filename=target_path.name,
            rights_confirmed=True,
        ),
        (target_path.read_bytes(),),
    )
    other = imports.import_pdf(
        PdfImportInput(
            work_id=None,
            title="API other paper",
            authors=("Other Author",),
            doi=None,
            original_filename=other_path.name,
            rights_confirmed=True,
        ),
        (other_path.read_bytes(),),
    )
    _persist_extraction(app.state.sessions, source.document_version_id, source_path)
    _persist_extraction(app.state.sessions, target.document_version_id, target_path)
    _persist_extraction(app.state.sessions, other.document_version_id, other_path)
    annotation = AnnotationRepository(app.state.sessions, clock=lambda: NOW).create(
        source.document_version_id,
        AnnotationInput(
            document_content_sha256=source.content_sha256,
            kind=AnnotationKind.HIGHLIGHT,
            page_number=1,
            page_count=1,
            page_width=420,
            page_height=595,
            quote_text="API migration evidence.",
            context_before="Before. ",
            context_after=" After.",
            rects=(PdfRect(x0=48, y0=48, x1=240, y1=82),),
            color=AnnotationColor.AMBER,
            note=None,
        ),
    )
    return app, paths, source, target, other, annotation


@pytest.mark.asyncio
async def test_annotation_migration_api_previews_then_requires_explicit_csrf_decision(
    tmp_path: Path,
) -> None:
    app, paths, source, target, _, annotation = migration_app(tmp_path)
    preview_url = f"/api/v1/documents/{source.document_version_id}/annotation-migrations/preview"
    async with local_client(app) as client:
        headers = await bootstrap(client)
        missing_csrf = await client.post(
            preview_url,
            json={"target_document_version_id": target.document_version_id},
            headers={"Origin": headers["Origin"]},
        )
        preview_response = await client.post(
            preview_url,
            json={"target_document_version_id": target.document_version_id},
            headers=headers,
        )
        assert missing_csrf.status_code == 403
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["source_document_version_id"] == source.document_version_id
        assert preview["target_document_version_id"] == target.document_version_id
        assert preview["coverage"] == {
            "total": 1,
            "matched": 1,
            "ambiguous": 0,
            "orphaned": 0,
            "pending": 1,
            "accepted": 0,
            "rejected": 0,
        }
        candidate = preview["candidates"][0]
        assert candidate["source_annotation_id"] == annotation.id
        assert candidate["decision"] is None

        target_before = await client.get(
            f"/api/v1/documents/{target.document_version_id}/annotations"
        )
        restored = await client.get(f"/api/v1/annotation-migrations/{preview['id']}")
        assert target_before.status_code == 200 and target_before.json() == []
        assert restored.status_code == 200 and restored.json()["id"] == preview["id"]

        decision_url = f"/api/v1/annotation-migrations/candidates/{candidate['id']}/decision"
        decision_without_csrf = await client.put(
            decision_url,
            json={"decision": "accept"},
            headers={"Origin": headers["Origin"]},
        )
        invalid_decision = await client.put(
            decision_url,
            json={"decision": "maybe"},
            headers=headers,
        )
        accepted = await client.put(
            decision_url,
            json={"decision": "accept"},
            headers=headers,
        )
        duplicate = await client.put(
            decision_url,
            json={"decision": "accept"},
            headers=headers,
        )
        assert decision_without_csrf.status_code == 403
        assert invalid_decision.status_code == 422
        assert accepted.status_code == 200
        assert accepted.json()["decision"] == "accept"
        assert accepted.json()["target_annotation_id"] is not None
        assert duplicate.status_code == 409

        target_after = await client.get(
            f"/api/v1/documents/{target.document_version_id}/annotations"
        )
        assert target_after.status_code == 200
        assert len(target_after.json()) == 1
        assert target_after.json()[0]["quote_text"] == "API migration evidence."
        for response in (missing_csrf, invalid_decision, duplicate):
            assert str(paths.library_root) not in response.text


@pytest.mark.asyncio
async def test_annotation_migration_api_has_safe_pair_and_not_found_boundaries(
    tmp_path: Path,
) -> None:
    app, paths, source, target, other, _ = migration_app(tmp_path)
    async with local_client(app) as client:
        headers = await bootstrap(client)
        same_document = await client.post(
            f"/api/v1/documents/{source.document_version_id}/annotation-migrations/preview",
            json={"target_document_version_id": source.document_version_id},
            headers=headers,
        )
        wrong_work = await client.post(
            f"/api/v1/documents/{source.document_version_id}/annotation-migrations/preview",
            json={"target_document_version_id": other.document_version_id},
            headers=headers,
        )
        missing_source = await client.post(
            "/api/v1/documents/missing-document/annotation-migrations/preview",
            json={"target_document_version_id": target.document_version_id},
            headers=headers,
        )
        missing_preview = await client.get("/api/v1/annotation-migrations/missing-preview")
        missing_candidate = await client.put(
            "/api/v1/annotation-migrations/candidates/missing-candidate/decision",
            json={"decision": "reject"},
            headers=headers,
        )

        assert same_document.status_code == 409
        assert wrong_work.status_code == 409
        assert missing_source.status_code == 404
        assert missing_preview.status_code == 404
        assert missing_candidate.status_code == 404
        for response in (
            same_document,
            wrong_work,
            missing_source,
            missing_preview,
            missing_candidate,
        ):
            assert str(paths.library_root) not in response.text
