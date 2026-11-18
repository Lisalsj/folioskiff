from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths

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
    }


def add_old_disliked_document(app, label: str):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord, LibraryStateRecord
    from paper_reader.reader.models import LibraryEntryRecord
    from paper_reader.reader.storage import ContentStore

    stored = ContentStore(app.state.paths.library_root).ingest(
        (b"%PDF-1.7\n% retention API " + label.encode() + b"\n%%EOF\n",),
        media_type="application/pdf",
        max_bytes=1024,
    )
    old = NOW - timedelta(days=100)
    ingested = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="fixture",
            external_id=f"retention-api-{label}",
            title=f"Retention API {label}",
            authors=("Alice",),
        )
    )
    with app.state.sessions() as session:
        document = DocumentVersionRecord(
            work_id=ingested.work.id,
            source_record_id=ingested.source_record.id,
            content_sha256=stored.content_sha256,
            access_status="open",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=old,
            extraction_status="complete",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename=f"{label}.pdf",
            acquisition_kind="download",
            acquisition_url=f"https://example.org/{label}.pdf",
            last_accessed_at=old,
            created_at=old,
        )
        session.add(document)
        session.add(
            LibraryEntryRecord(
                work_id=ingested.work.id,
                added_via="save",
                created_at=old,
                updated_at=old,
            )
        )
        session.add(
            LibraryStateRecord(
                work_id=ingested.work.id,
                favorite=False,
                feedback="dislike",
                feedback_reasons=["not relevant"],
                pinned=False,
                reading_progress=0,
                analysis_state="not_analyzed",
                created_at=old,
                updated_at=old,
            )
        )
        session.commit()
    return ingested.work.id, stored


@pytest.mark.asyncio
async def test_cleanup_api_requires_session_csrf_preview_and_explicit_apply(
    tmp_path: Path,
) -> None:
    app = reader_app(tmp_path)
    work_id, stored = add_old_disliked_document(app, "clean")

    async with local_client(app) as client:
        unauthorized = await client.post(
            "/api/v1/library/cache-cleanup/preview",
            json={"retention_days": 90},
        )
        headers = await bootstrap(client)
        no_csrf_preview = await client.post(
            "/api/v1/library/cache-cleanup/preview",
            json={"retention_days": 90},
            headers={"Origin": "http://127.0.0.1"},
        )
        preview = await client.post(
            "/api/v1/library/cache-cleanup/preview",
            json={"retention_days": 90},
            headers=headers,
        )
        assert preview.status_code == 200
        token = preview.json()["preview_token"]
        no_csrf_apply = await client.post(
            "/api/v1/library/cache-cleanup/apply",
            json={"preview_token": token},
            headers={"Origin": "http://127.0.0.1"},
        )
        applied = await client.post(
            "/api/v1/library/cache-cleanup/apply",
            json={"preview_token": token},
            headers=headers,
        )
        repeated = await client.post(
            "/api/v1/library/cache-cleanup/apply",
            json={"preview_token": token},
            headers=headers,
        )
        documents = await client.get(f"/api/v1/works/{work_id}/documents")

    assert unauthorized.status_code == 401
    assert no_csrf_preview.status_code == 403
    assert no_csrf_apply.status_code == 403
    assert preview.json()["eligible_count"] == 1
    assert preview.json()["candidates"][0]["eligible"] is True
    assert "relative_path" not in preview.text
    assert applied.status_code == 200
    assert applied.json() == repeated.json()
    assert applied.json()["deleted_objects"] == 1
    assert applied.json()["cleared_document_versions"] == 1
    assert documents.status_code == 200
    assert documents.json()[0]["available"] is False
    assert not ContentPath(app.state.paths.library_root, stored.relative_path).exists()


class ContentPath:
    def __init__(self, root: Path, relative_path: str) -> None:
        self.path = root / Path(relative_path)

    def exists(self) -> bool:
        return self.path.exists()


@pytest.mark.asyncio
async def test_cleanup_api_rejects_unknown_or_stale_preview_and_invalid_days(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.models import LibraryStateRecord

    app = reader_app(tmp_path)
    work_id, _ = add_old_disliked_document(app, "stale")
    async with local_client(app) as client:
        headers = await bootstrap(client)
        invalid_days = await client.post(
            "/api/v1/library/cache-cleanup/preview",
            json={"retention_days": 0},
            headers=headers,
        )
        unknown = await client.post(
            "/api/v1/library/cache-cleanup/apply",
            json={"preview_token": "0" * 64},
            headers=headers,
        )
        preview = await client.post(
            "/api/v1/library/cache-cleanup/preview",
            json={"retention_days": 90},
            headers=headers,
        )
        with app.state.sessions() as session:
            state = session.get(LibraryStateRecord, work_id)
            assert state is not None
            state.pinned = True
            session.commit()
        stale = await client.post(
            "/api/v1/library/cache-cleanup/apply",
            json={"preview_token": preview.json()["preview_token"]},
            headers=headers,
        )

    assert invalid_days.status_code == 422
    assert unknown.status_code == 409
    assert stale.status_code == 409
    assert "preview" in stale.json()["detail"].casefold()
