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
    await client.post(
        "/api/v1/session/bootstrap",
        json={"token": "secret"},
        headers={"Origin": "http://127.0.0.1"},
    )
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": client.cookies["paper_reader_csrf"],
    }


@pytest.mark.asyncio
async def test_rating_catalog_api_imports_lists_and_deactivates_a_local_catalog(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )

    async with local_client(app) as client:
        headers = await bootstrap(client)
        rejected = await client.post(
            "/api/v1/scores/rating-catalogs/import",
            data={
                "kind": "jcr",
                "catalog_name": "JCR local",
                "catalog_version": "2025",
                "source_note": "Licensed export",
                "rights_confirmed": "false",
            },
            files={"file": ("jcr.csv", b"venue,rating\nJournal One,Q1\n", "text/csv")},
            headers=headers,
        )
        imported = await client.post(
            "/api/v1/scores/rating-catalogs/import",
            data={
                "kind": "jcr",
                "catalog_name": "JCR local",
                "catalog_version": "2025",
                "source_note": "Licensed export",
                "rights_confirmed": "true",
            },
            files={"file": ("jcr.csv", b"venue,rating\nJournal One,Q1\n", "text/csv")},
            headers=headers,
        )
        listed = await client.get("/api/v1/scores/rating-catalogs")
        removed = await client.delete(
            "/api/v1/scores/rating-catalogs/jcr/active",
            headers=headers,
        )
        after = await client.get("/api/v1/scores/rating-catalogs")

    assert rejected.status_code == 422
    assert "rights" in rejected.json()["detail"]
    assert imported.status_code == 201
    assert imported.json()["kind"] == "jcr"
    assert imported.json()["row_count"] == 1
    assert imported.json()["active"] is True
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert removed.status_code == 204
    assert after.json()[0]["active"] is False


@pytest.mark.asyncio
async def test_ccf_catalog_api_exposes_version_and_provenance_without_all_rows(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )
    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get("/api/v1/scores/rating-catalogs/ccf")

    assert response.status_code == 200
    assert response.json() == {
        "catalog_id": "ccf-2022-sixth-2024-rename",
        "edition": 6,
        "rating_year": 2022,
        "rename_revision_date": "2024-06-28",
        "entry_count": 649,
        "journal_count": 284,
        "conference_count": 365,
        "source_url": (
            "https://www.ccf.org.cn/Academic_Evaluation/By_category/2024-06-28/825349.shtml"
        ),
        "source_pdf_sha256": (
            "9390786c231888ad441b639bc5ae734b5809a8b86602bd85ef220f2073913bca"
        ),
    }
