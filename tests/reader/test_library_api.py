from __future__ import annotations

import json
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
    }


@pytest.mark.asyncio
async def test_library_collection_requires_a_local_session(tmp_path: Path) -> None:
    app = reader_app(tmp_path)

    async with local_client(app) as client:
        response = await client.get("/api/v1/library/works")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_reference_import_lists_and_reads_a_saved_work_without_route_collision(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        imported = await client.post(
            "/api/v1/library/imports/reference",
            json={
                "kind": "doi",
                "value": "https://doi.org/10.1000/Reader.Api",
                "title": "Agentic Paper Reading",
                "authors": ["Alice Chen"],
                "rights_confirmed": False,
            },
            headers=headers,
        )
        assert imported.status_code == 201
        work_id = imported.json()["work_id"]
        collection = await client.get(
            "/api/v1/library/works",
            params={"query": "alice", "limit": 20, "offset": 0},
        )
        detail = await client.get(f"/api/v1/works/{work_id}")
        documents = await client.get(f"/api/v1/works/{work_id}/documents")
        preference_state = await client.get(f"/api/v1/library/works/{work_id}")

    assert imported.json() == {
        "work_id": work_id,
        "created_work": True,
        "kind": "doi",
        "normalized_value": "10.1000/reader.api",
    }
    assert collection.status_code == 200
    assert collection.json()["total"] == 1
    assert collection.json()["items"][0]["id"] == work_id
    assert collection.json()["items"][0]["favorite"] is False
    assert detail.status_code == 200
    assert detail.json()["title"] == "Agentic Paper Reading"
    assert detail.json()["representative_document"] is None
    assert documents.status_code == 200
    assert documents.json() == []
    assert preference_state.status_code == 200
    assert preference_state.json()["persisted"] is False
    assert "added_via" not in preference_state.json()
    assert "relative_path" not in json.dumps(collection.json())
    assert "relative_path" not in json.dumps(detail.json())


@pytest.mark.asyncio
async def test_pdf_upload_is_bounded_idempotent_and_exposes_no_local_path(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        form = {
            "title": "Uploaded Reader Paper",
            "authors": json.dumps(["Alice Chen", "Bob Kumar"]),
            "doi": "10.1000/uploaded",
            "rights_confirmed": "true",
        }
        first = await client.post(
            "/api/v1/library/imports/pdf",
            data=form,
            files={"file": ("reader.pdf", PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        repeated = await client.post(
            "/api/v1/library/imports/pdf",
            data=form,
            files={"file": ("reader.pdf", PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        assert first.status_code == 201
        documents = await client.get(
            f"/api/v1/works/{first.json()['work_id']}/documents"
        )

    assert first.json()["created_work"] is True
    assert first.json()["created_document"] is True
    assert repeated.status_code == 201
    assert repeated.json()["work_id"] == first.json()["work_id"]
    assert repeated.json()["document_version_id"] == first.json()["document_version_id"]
    assert repeated.json()["created_work"] is False
    assert repeated.json()["created_document"] is False
    assert documents.status_code == 200
    assert len(documents.json()) == 1
    assert documents.json()[0]["available"] is True
    assert documents.json()[0]["original_filename"] == "reader.pdf"
    assert "relative_path" not in documents.json()[0]


@pytest.mark.asyncio
async def test_library_import_mutations_validate_csrf_rights_work_and_content(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        missing_csrf = await client.post(
            "/api/v1/library/imports/reference",
            json={"kind": "doi", "value": "10.1000/no-csrf"},
            headers={"Origin": "http://127.0.0.1"},
        )
        invalid_url = await client.post(
            "/api/v1/library/imports/reference",
            json={"kind": "url", "value": "http://127.0.0.1/private.pdf"},
            headers=headers,
        )
        no_rights = await client.post(
            "/api/v1/library/imports/pdf",
            data={"rights_confirmed": "false"},
            files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        missing_work = await client.post(
            "/api/v1/library/imports/pdf",
            data={"work_id": "missing-work", "rights_confirmed": "true"},
            files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        invalid_pdf = await client.post(
            "/api/v1/library/imports/pdf",
            data={"title": "Not PDF", "rights_confirmed": "true"},
            files={"file": ("paper.pdf", b"not a pdf", "application/pdf")},
            headers=headers,
        )
        invalid_authors = await client.post(
            "/api/v1/library/imports/pdf",
            data={
                "title": "Invalid Authors",
                "authors": "not-json",
                "rights_confirmed": "true",
            },
            files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        wrong_media_type = await client.post(
            "/api/v1/library/imports/pdf",
            data={"title": "Wrong Media Type", "rights_confirmed": "true"},
            files={"file": ("paper.txt", PDF_BYTES, "text/plain")},
            headers=headers,
        )
        missing_detail = await client.get("/api/v1/works/missing-work")
        missing_documents = await client.get(
            "/api/v1/works/missing-work/documents"
        )

    assert missing_csrf.status_code == 403
    assert invalid_url.status_code == 422
    assert no_rights.status_code == 422
    assert missing_work.status_code == 404
    assert invalid_pdf.status_code == 422
    assert invalid_authors.status_code == 422
    assert wrong_media_type.status_code == 422
    assert missing_detail.status_code == 404
    assert missing_documents.status_code == 404
