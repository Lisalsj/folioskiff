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


def make_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


class FixtureConnector:
    def __init__(self) -> None:
        from paper_reader.discovery.connectors import ConnectorCapabilities

        self.capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="arXiv fixture",
            credential_requirement="none",
            supports_exact_updated_date=True,
            protocol_version="fixture-v1",
        )
        self.fetch_calls = 0

    async def estimate(self, query):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.connectors import ConnectorEstimate

        return ConnectorEstimate(total=1, exact=True)

    async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.connectors import SourcePage
        from paper_reader.discovery.identity import SourcePaper

        self.fetch_calls += 1
        return SourcePage(
            records=(
                SourcePaper(
                    connector_id="arxiv",
                    external_id="2607.12345v1",
                    title="Reliable AI agent for paper reading",
                    authors=("Alice Researcher",),
                    updated_at=datetime(2026, 7, 13, tzinfo=UTC),
                ),
            ),
            next_cursor=None,
            total=1,
        )


def create_plan(app: Any):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        DiscoveryPlanRepository,
    )

    keywords = KeywordRepository(app.state.sessions)
    version = keywords.create_version(
        research_intent="追踪论文阅读智能体",
        definition=KeywordSetDefinition(
            required=(ConceptGroup(key="agent", label="智能体", terms=("AI agent", "智能体")),)
        ),
    )
    keywords.confirm(version.id)
    return DiscoveryPlanRepository(app.state.sessions).create(
        DiscoveryPlanCreate(
            name="Agent reading",
            research_intent="追踪论文阅读智能体",
            keyword_version_id=version.id,
            date_range=DateRangeSpec(
                mode="fixed",
                start_month="2026-07",
                end_month="2026-07",
            ),
            sources=("arxiv",),
        )
    )


async def wait_for_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("background job did not finish")


@pytest.mark.asyncio
async def test_source_capabilities_show_five_official_sources_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.app import create_app

    monkeypatch.delenv("IEEE_XPLORE_API_KEY", raising=False)
    monkeypatch.delenv("SPRINGER_NATURE_API_KEY", raising=False)
    app = create_app(paths=make_paths(tmp_path), bootstrap_token="secret")

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        response = await client.get("/api/v1/settings/sources")

    assert response.status_code == 200
    sources = {item["connector_id"]: item for item in response.json()}
    assert set(sources) == {
        "arxiv",
        "openreview",
        "europe_pmc",
        "ieee",
        "springer_nature",
    }
    assert sources["arxiv"]["enabled"] is True
    assert sources["ieee"]["enabled"] is False
    assert sources["ieee"]["disabled_reason"] == "missing_api_key"
    assert sources["springer_nature"]["enabled"] is False
    assert all("api_key" not in item for item in response.json())


@pytest.mark.asyncio
async def test_preview_and_run_return_202_jobs_with_persisted_results_and_idempotency(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.identity import DiscoveryKind, IdentityRepository, MatchKind
    from paper_reader.discovery.runs import CandidateIngest, DiscoveryRunRepository

    connector = FixtureConnector()
    registry = ConnectorRegistry((connector,))
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="secret",
        connector_registry=registry,
    )
    plan = create_plan(app)

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
        preview = await client.post(
            f"/api/v1/plans/{plan.id}/preview",
            headers={**mutation_headers, "Idempotency-Key": "preview-once"},
        )
        preview_job = await wait_for_job(client, preview.json()["id"])
        started = await client.post(
            f"/api/v1/plans/{plan.id}/runs",
            headers={**mutation_headers, "Idempotency-Key": "run-once"},
        )
        duplicate = await client.post(
            f"/api/v1/plans/{plan.id}/runs",
            headers={**mutation_headers, "Idempotency-Key": "run-once"},
        )
        run_job = await wait_for_job(client, started.json()["id"])
        run_response = await client.get(f"/api/v1/runs/{run_job['result']['run_id']}")

    assert preview.status_code == 202
    assert preview_job["status"] == "succeeded"
    assert preview_job["result"]["sources"][0]["samples"][0]["title"].startswith(
        "Reliable AI agent"
    )
    assert started.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == started.json()["id"]
    assert run_job["status"] == "succeeded"
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "succeeded"
    assert run_response.json()["batches"][0]["accepted_count"] == 1
    assert run_response.json()["batches"][0]["new_work_count"] == 1
    assert run_response.json()["batches"][0]["update_count"] == 0
    assert run_response.json()["batches"][0]["duplicate_count"] == 0
    assert run_response.json()["batches"][0]["scanned_count"] == 1
    assert run_response.json()["batches"][0]["filter_counts"] == {
        "evaluated_count": 1,
        "missing_updated_at_count": 0,
        "outside_date_range_count": 0,
        "in_date_range_count": 1,
        "keyword_mismatch_count": 0,
        "accepted_count": 1,
    }
    assert IdentityRepository(app.state.sessions).count_works() == 1
    repository = DiscoveryRunRepository(app.state.sessions)
    run_id = run_job["result"]["run_id"]
    candidate_records = repository.list_candidates(run_id)
    candidates = repository.list_candidate_work_ids(run_id)
    assert len(candidates) == 1
    assert len(candidate_records) == 1
    candidate = candidate_records[0]
    repository.record_candidates(
        run_id=run_id,
        batch_id=candidate.batch_id,
        candidates=(
            CandidateIngest(
                work_id=candidate.work_id,
                source_record_id=candidate.source_record_id,
                match_kind=MatchKind(candidate.match_kind),
                discovery_kind=DiscoveryKind(candidate.discovery_kind),
            ),
        ),
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )
    assert repository.list_candidates(run_id) == candidate_records
    assert app.state.lexical_index.search("paper reading agent")[0].work_id == candidates[0]


@pytest.mark.asyncio
async def test_application_start_resumes_a_queued_discovery_run(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.discovery.plans import DiscoveryPlanRepository
    from paper_reader.discovery.runs import DiscoveryRunRepository, DiscoveryRunService
    from paper_reader.jobs.repository import JobRepository

    connector = FixtureConnector()
    registry = ConnectorRegistry((connector,))
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="secret",
        connector_registry=registry,
    )
    plan = create_plan(app)
    queued = DiscoveryRunService(
        plans=DiscoveryPlanRepository(app.state.sessions),
        keywords=KeywordRepository(app.state.sessions),
        connectors=registry,
        identities=IdentityRepository(app.state.sessions),
        jobs=JobRepository(app.state.sessions),
        runs=DiscoveryRunRepository(app.state.sessions),
    ).create(plan.id, idempotency_key="resume-on-open")

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        completed = await wait_for_job(client, queued.job_id)

    assert completed["status"] == "succeeded"
    assert connector.fetch_calls == 1


@pytest.mark.asyncio
async def test_draft_preview_uses_confirmed_keywords_without_persisting_a_plan(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )

    connector = FixtureConnector()
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="secret",
        connector_registry=ConnectorRegistry((connector,)),
    )
    keywords = KeywordRepository(app.state.sessions)
    version = keywords.create_version(
        research_intent="追踪论文阅读智能体",
        definition=KeywordSetDefinition(
            required=(ConceptGroup(key="agent", label="智能体", terms=("AI agent", "智能体")),)
        ),
    )
    keywords.confirm(version.id)
    draft = {
        "name": "尚未保存的计划",
        "research_intent": "追踪论文阅读智能体",
        "keyword_version_id": version.id,
        "date_range": {
            "mode": "fixed",
            "start_month": "2026-07",
            "end_month": "2026-07",
            "timezone": "Asia/Shanghai",
        },
        "sources": ["arxiv"],
    }

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        preview = await client.post(
            "/api/v1/plans/preview",
            json=draft,
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )
        completed = await wait_for_job(client, preview.json()["id"])
        plans = await client.get("/api/v1/plans")

    assert preview.status_code == 202
    assert completed["status"] == "succeeded"
    assert completed["result"]["plan_id"].startswith("draft:")
    assert plans.json() == []
