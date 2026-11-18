from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths


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


def annotation_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app
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
    )

    def add(label: str):  # type: ignore[no-untyped-def]
        return imports.import_pdf(
            PdfImportInput(
                work_id=None,
                title=f"API annotations {label}",
                authors=("Alice Chen",),
                doi=None,
                original_filename=f"{label}.pdf",
                rights_confirmed=True,
            ),
            (f"%PDF-1.7\n{label}\n%%EOF\n".encode(),),
        )

    first = add("first")
    second = add("second")
    return app, first, second


def content_hash(app: Any, document_id: str) -> str:
    from paper_reader.discovery.models import DocumentVersionRecord

    with app.state.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        assert document is not None
        return document.content_sha256


def position_payload(hash_value: str, *, page: int = 2, page_count: int = 5) -> dict[str, Any]:
    return {
        "document_content_sha256": hash_value,
        "page_number": page,
        "page_count": page_count,
        "scale": 1.25,
        "rotation": 90,
    }


def annotation_payload(
    hash_value: str,
    *,
    kind: str = "highlight",
    page: int = 2,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "document_content_sha256": hash_value,
        "kind": kind,
        "page_number": page,
        "page_count": 5,
        "page_width": 420,
        "page_height": 595,
        "quote_text": None if kind == "bookmark" else "exact API evidence",
        "context_before": None if kind == "bookmark" else "before",
        "context_after": None if kind == "bookmark" else "after",
        "rects": []
        if kind == "bookmark"
        else [{"x0": 12, "y0": 20, "x1": 180, "y1": 38}],
        "color": "amber",
        "note": note,
    }


@pytest.mark.asyncio
async def test_reading_position_api_restores_idempotently_and_requires_csrf(
    tmp_path: Path,
) -> None:
    app, first, _ = annotation_app(tmp_path)
    document_id = first.document_version_id
    hash_value = content_hash(app, document_id)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        empty = await client.get(f"/api/v1/documents/{document_id}/reading-position")
        missing_csrf = await client.put(
            f"/api/v1/documents/{document_id}/reading-position",
            json=position_payload(hash_value),
            headers={"Origin": "http://127.0.0.1"},
        )
        saved = await client.put(
            f"/api/v1/documents/{document_id}/reading-position",
            json=position_payload(hash_value),
            headers=headers,
        )
        repeated = await client.put(
            f"/api/v1/documents/{document_id}/reading-position",
            json=position_payload(hash_value),
            headers=headers,
        )
        restored = await client.get(
            f"/api/v1/documents/{document_id}/reading-position"
        )
        stale = await client.put(
            f"/api/v1/documents/{document_id}/reading-position",
            json=position_payload("f" * 64),
            headers=headers,
        )
        invalid_page = await client.put(
            f"/api/v1/documents/{document_id}/reading-position",
            json=position_payload(hash_value, page=6, page_count=5),
            headers=headers,
        )
        missing = await client.get(
            "/api/v1/documents/missing-document/reading-position"
        )

    assert empty.status_code == 200 and empty.json() is None
    assert missing_csrf.status_code == 403
    assert saved.status_code == 200
    assert saved.json()["progress"] == 40
    assert saved.json()["rotation"] == 90
    assert repeated.json()["updated_at"] == saved.json()["updated_at"]
    assert restored.json() == saved.json()
    assert stale.status_code == 409
    assert invalid_page.status_code == 422
    assert missing.status_code == 404
    assert str(app.state.paths.library_root) not in stale.text


@pytest.mark.asyncio
async def test_annotation_api_crud_is_owned_ordered_filterable_and_safe(
    tmp_path: Path,
) -> None:
    app, first, second = annotation_app(tmp_path)
    document_id = first.document_version_id
    other_document_id = second.document_version_id
    hash_value = content_hash(app, document_id)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        highlight = await client.post(
            f"/api/v1/documents/{document_id}/annotations",
            json=annotation_payload(hash_value, page=2),
            headers=headers,
        )
        bookmark = await client.post(
            f"/api/v1/documents/{document_id}/annotations",
            json=annotation_payload(
                hash_value,
                kind="bookmark",
                page=1,
                note="Return here",
            ),
            headers=headers,
        )
        listed = await client.get(
            f"/api/v1/documents/{document_id}/annotations"
        )
        filtered = await client.get(
            f"/api/v1/documents/{document_id}/annotations?kind=bookmark"
        )
        annotation_id = highlight.json()["id"]
        revised_payload = annotation_payload(hash_value, page=2, note="My note")
        revised_payload["color"] = "blue"
        revised = await client.put(
            f"/api/v1/documents/{document_id}/annotations/{annotation_id}",
            json=revised_payload,
            headers=headers,
        )
        wrong_owner = await client.put(
            f"/api/v1/documents/{other_document_id}/annotations/{annotation_id}",
            json={
                **revised_payload,
                "document_content_sha256": content_hash(app, other_document_id),
            },
            headers=headers,
        )
        stale = await client.post(
            f"/api/v1/documents/{document_id}/annotations",
            json=annotation_payload("f" * 64),
            headers=headers,
        )
        outside = annotation_payload(hash_value)
        outside["rects"] = [{"x0": 0, "y0": 0, "x1": 421, "y1": 10}]
        invalid = await client.post(
            f"/api/v1/documents/{document_id}/annotations",
            json=outside,
            headers=headers,
        )
        missing_csrf = await client.delete(
            f"/api/v1/documents/{document_id}/annotations/{annotation_id}",
            headers={"Origin": "http://127.0.0.1"},
        )
        deleted = await client.delete(
            f"/api/v1/documents/{document_id}/annotations/{annotation_id}",
            headers=headers,
        )
        missing = await client.delete(
            f"/api/v1/documents/{document_id}/annotations/{annotation_id}",
            headers=headers,
        )

    assert highlight.status_code == 201
    assert highlight.json()["document_content_sha256"] == hash_value
    assert bookmark.status_code == 201
    assert [item["page_number"] for item in listed.json()] == [1, 2]
    assert [item["id"] for item in filtered.json()] == [bookmark.json()["id"]]
    assert revised.status_code == 200
    assert revised.json()["color"] == "blue"
    assert revised.json()["note"] == "My note"
    assert wrong_owner.status_code == 404
    assert stale.status_code == 409
    assert invalid.status_code == 422
    assert missing_csrf.status_code == 403
    assert deleted.status_code == 204
    assert missing.status_code == 404
    for response in (wrong_owner, stale, invalid, missing):
        assert str(app.state.paths.library_root) not in response.text
