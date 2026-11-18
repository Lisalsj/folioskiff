from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths


class FakeEnrichmentService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.executed: list[str] = []

    def create_job(
        self,
        run_id: str,
        *,
        enricher_ids: tuple[str, ...] | None,
        idempotency_key: str | None,
    ) -> Any:
        self.created.append(
            {
                "run_id": run_id,
                "enricher_ids": enricher_ids,
                "idempotency_key": idempotency_key,
            }
        )
        return SimpleNamespace(
            id="metadata-job-1",
            kind="recommendation.metadata_enrichment",
            status="queued",
            progress=0,
            cancel_requested=False,
        )

    async def execute(self, job_id: str) -> None:
        self.executed.append(job_id)


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


@pytest.mark.asyncio
async def test_metadata_enrichment_command_returns_202_and_starts_durable_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.app import create_app

    fake = FakeEnrichmentService()
    monkeypatch.setattr("paper_reader.recommendation.api._service", lambda request: fake)
    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.post(
            "/api/v1/scores/runs/run-1/metadata",
            json={"enricher_ids": ["openalex", "crossref"]},
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": client.cookies["paper_reader_csrf"],
                "Idempotency-Key": "button-click-1",
            },
        )
        await asyncio.sleep(0)

    assert response.status_code == 202
    assert response.json() == {
        "id": "metadata-job-1",
        "kind": "recommendation.metadata_enrichment",
        "status": "queued",
        "progress": 0,
        "cancel_requested": False,
    }
    assert fake.created == [
        {
            "run_id": "run-1",
            "enricher_ids": ("openalex", "crossref"),
            "idempotency_key": "metadata.enrichment:button-click-1",
        }
    ]
    assert fake.executed == ["metadata-job-1"]
