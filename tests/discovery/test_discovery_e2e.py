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


@asynccontextmanager
async def local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


async def wait_for_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        payload: dict[str, Any] = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("background job did not finish")


class IntentFixtureConnector:
    def __init__(self) -> None:
        from paper_reader.discovery.connectors import ConnectorCapabilities

        self.capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="arXiv fixture",
            credential_requirement="none",
            supports_exact_updated_date=True,
            protocol_version="intent-e2e-v1",
        )

    async def estimate(self, query):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.connectors import ConnectorEstimate

        return ConnectorEstimate(total=1, exact=True)

    async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.connectors import SourcePage
        from paper_reader.discovery.identity import SourcePaper

        return SourcePage(
            records=(
                SourcePaper(
                    connector_id="arxiv",
                    external_id="2607.42424v1",
                    title="Reliable AI agent with a diffusion model for paper reading",
                    authors=("Alice Researcher",),
                    updated_at=datetime(2026, 7, 13, tzinfo=UTC),
                ),
            ),
            next_cursor=None,
            total=1,
        )


@pytest.mark.asyncio
async def test_research_intent_to_confirmed_preview_and_persisted_run(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.identity import IdentityRepository

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        connector_registry=ConnectorRegistry((IntentFixtureConnector(),)),
    )

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        mutation_headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": csrf,
        }
        suggestion = await client.post(
            "/api/v1/keywords/suggestions",
            json={
                "research_intent": "研究 diffusion agent 的可靠论文阅读方法",
                "mode": "manual",
                "target_count": 18,
            },
            headers=mutation_headers,
        )
        keyword_id = suggestion.json()["id"]
        confirmed = await client.post(
            f"/api/v1/keywords/versions/{keyword_id}/confirm",
            headers=mutation_headers,
        )
        plan_payload = {
            "name": "扩散智能体阅读",
            "research_intent": "研究 diffusion agent 的可靠论文阅读方法",
            "keyword_version_id": keyword_id,
            "date_range": {
                "mode": "fixed",
                "start_month": "2026-07",
                "end_month": "2026-07",
                "timezone": "Asia/Shanghai",
            },
            "sources": ["arxiv"],
            "source_filters": {"arxiv": {"categories": ["cs.AI"]}},
        }
        preview = await client.post(
            "/api/v1/plans/preview",
            json=plan_payload,
            headers=mutation_headers,
        )
        preview_job = await wait_for_job(client, preview.json()["id"])
        created = await client.post(
            "/api/v1/plans",
            json=plan_payload,
            headers=mutation_headers,
        )
        started = await client.post(
            f"/api/v1/plans/{created.json()['id']}/runs",
            headers=mutation_headers,
        )
        run_job = await wait_for_job(client, started.json()["id"])

    assert suggestion.status_code == 201
    assert suggestion.json()["generator_info"]["intent_used_for_filtering"] is False
    assert confirmed.json()["confirmed_at"] is not None
    assert preview.status_code == 202
    assert preview_job["status"] == "succeeded"
    assert preview_job["result"]["sources"][0]["samples"][0]["external_id"] == (
        "2607.42424v1"
    )
    assert created.status_code == 201
    assert started.status_code == 202
    assert run_job["status"] == "succeeded"
    assert IdentityRepository(app.state.sessions).count_works() == 1
