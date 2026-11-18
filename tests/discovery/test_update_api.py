from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths

NOW = datetime(2026, 7, 15, 4, tzinfo=UTC)


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


class RevisionConnector:
    def __init__(self, *, revised: bool) -> None:
        from paper_reader.discovery.connectors import ConnectorCapabilities

        self.revised = revised
        self.capabilities = ConnectorCapabilities(
            connector_id="arxiv",
            display_name="arXiv revision fixture",
            credential_requirement="none",
            supports_exact_updated_date=True,
            protocol_version="fixture-v1",
        )

    async def estimate(self, query):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.connectors import ConnectorEstimate

        return ConnectorEstimate(total=1 if self.revised else 0, exact=True)

    async def fetch_page(self, query, *, cursor, limit):  # type: ignore[no-untyped-def]
        from paper_reader.discovery.connectors import SourcePage
        from paper_reader.discovery.identity import SourcePaper

        records = ()
        if self.revised:
            records = (
                SourcePaper(
                    connector_id="arxiv",
                    external_id="2607.40001v1",
                    title="Reliable agent paper reading",
                    authors=("Alice Researcher", "Bob Researcher"),
                    abstract="Expanded evaluation and evidence.",
                    doi="10.1000/reader-update",
                    updated_at=datetime(2026, 7, 14, tzinfo=UTC),
                    raw_payload={"private_fixture_detail": "must-not-leak"},
                ),
            )
        return SourcePage(records=records, next_cursor=None, total=len(records))


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
            required=(ConceptGroup(key="agent", label="智能体", terms=("agent",)),)
        ),
    )
    keywords.confirm(version.id)
    return DiscoveryPlanRepository(app.state.sessions).create(
        DiscoveryPlanCreate(
            name="Agent updates",
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


def create_run(  # type: ignore[no-untyped-def]
    app: Any,
    plan_id: str,
    *,
    connectors: Any,
    now: datetime,
    key: str,
):
    from paper_reader.discovery.identity import IdentityRepository
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.discovery.plans import DiscoveryPlanRepository
    from paper_reader.discovery.runs import DiscoveryRunRepository, DiscoveryRunService
    from paper_reader.jobs.repository import JobRepository

    return DiscoveryRunService(
        plans=DiscoveryPlanRepository(app.state.sessions),
        keywords=KeywordRepository(app.state.sessions),
        connectors=connectors,
        identities=IdentityRepository(app.state.sessions),
        jobs=JobRepository(app.state.sessions),
        runs=DiscoveryRunRepository(app.state.sessions),
    ).create(plan_id, idempotency_key=key, now=now)


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


async def wait_for_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("background job did not finish")


def seed_update_history(app: Any):  # type: ignore[no-untyped-def]
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper

    identities = IdentityRepository(app.state.sessions)
    baseline = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.40001v1",
            title="Reliable agent paper reading",
            authors=("Alice Researcher",),
            abstract="Initial evidence.",
            doi="10.1000/reader-update",
            updated_at=datetime(2026, 6, 10, tzinfo=UTC),
        ),
        now=NOW,
    )
    important = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.40001v1",
            title="Reliable agent paper reading",
            authors=("Alice Researcher",),
            abstract="Expanded evaluation and evidence.",
            doi="10.1000/reader-update",
            updated_at=datetime(2026, 7, 14, tzinfo=UTC),
            raw_payload={"private_fixture_detail": "must-not-leak"},
        ),
        now=NOW + timedelta(hours=1),
    )
    metadata = identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.40001v1",
            title="Reliable agent paper reading",
            authors=("Alice Researcher",),
            abstract="Expanded evaluation and evidence.",
            doi="10.1000/reader-update",
            updated_at=datetime(2026, 7, 14, tzinfo=UTC),
            landing_url="https://arxiv.org/abs/2607.40001",
            raw_payload={"private_fixture_detail": "still-must-not-leak"},
        ),
        now=NOW + timedelta(hours=2),
    )
    identities.add_document_version(
        work_id=baseline.work.id,
        source_record_id=baseline.source_record.id,
        content_sha256="a" * 64,
        access_status="open",
        completeness=100,
        source_updated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    return baseline, important, metadata


@pytest.mark.asyncio
async def test_plan_run_history_is_reverse_chronological_and_paginated(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry

    registry = ConnectorRegistry((RevisionConnector(revised=False),))
    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="secret",
        connector_registry=registry,
    )
    plan = create_plan(app)
    oldest = create_run(app, plan.id, connectors=registry, now=NOW, key="oldest")
    middle = create_run(
        app,
        plan.id,
        connectors=registry,
        now=NOW + timedelta(minutes=1),
        key="middle",
    )
    newest = create_run(
        app,
        plan.id,
        connectors=registry,
        now=NOW + timedelta(minutes=2),
        key="newest",
    )

    async with local_client(app) as client:
        await bootstrap(client)
        first = await client.get(f"/api/v1/plans/{plan.id}/runs?limit=2&offset=0")
        second = await client.get(f"/api/v1/plans/{plan.id}/runs?limit=2&offset=2")
        missing = await client.get("/api/v1/plans/missing-plan/runs")

    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert first.json()["limit"] == 2
    assert first.json()["offset"] == 0
    assert [item["id"] for item in first.json()["items"]] == [newest.id, middle.id]
    assert [item["id"] for item in second.json()["items"]] == [oldest.id]
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_global_update_api_hides_metadata_refreshes_and_private_evidence(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="secret")
    baseline, important, metadata = seed_update_history(app)
    assert important.paper_update_id is not None
    assert metadata.paper_update_id is not None

    async with local_client(app) as client:
        mutation_headers = await bootstrap(client)
        listed = await client.get("/api/v1/updates?limit=20&offset=0")
        missing_csrf = await client.put(
            f"/api/v1/updates/{important.paper_update_id}/state",
            json={"state": "read"},
        )
        invalid = await client.put(
            f"/api/v1/updates/{important.paper_update_id}/state",
            json={"state": "archived"},
            headers=mutation_headers,
        )
        changed = await client.put(
            f"/api/v1/updates/{important.paper_update_id}/state",
            json={"state": "read"},
            headers=mutation_headers,
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    item = listed.json()["items"][0]
    assert item["id"] == important.paper_update_id
    assert item["state"] == "unread"
    assert item["changed_fields"] == ["abstract", "source_updated_at", "raw_payload"]
    assert item["source"] == {
        "connector_id": "arxiv",
        "external_id": "2607.40001v1",
    }
    assert item["work"]["id"] == baseline.work.id
    assert item["work"]["title"] == "Reliable agent paper reading"
    assert item["has_readable_document"] is True
    assert item["source_updated_at"] == "2026-07-14T00:00:00Z"
    encoded = listed.text
    assert "private_fixture_detail" not in encoded
    assert "previous_payload_sha256" not in encoded
    assert "current_payload_sha256" not in encoded
    assert "relative_path" not in encoded
    assert missing_csrf.status_code == 403
    assert invalid.status_code == 422
    assert changed.status_code == 200
    assert changed.json()["state"] == "read"


@pytest.mark.asyncio
async def test_run_update_api_keeps_update_attribution_after_later_revisions(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper

    app = create_app(
        paths=make_paths(tmp_path),
        bootstrap_token="secret",
        connector_registry=ConnectorRegistry((RevisionConnector(revised=True),)),
    )
    identities = IdentityRepository(app.state.sessions)
    identities.ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.40001v1",
            title="Reliable agent paper reading",
            authors=("Alice Researcher",),
            abstract="Initial evidence.",
            doi="10.1000/reader-update",
            updated_at=datetime(2026, 6, 10, tzinfo=UTC),
        ),
        now=NOW,
    )
    plan = create_plan(app)

    async with local_client(app) as client:
        mutation_headers = await bootstrap(client)
        started = await client.post(
            f"/api/v1/plans/{plan.id}/runs",
            headers={**mutation_headers, "Idempotency-Key": "revision-run"},
        )
        job = await wait_for_job(client, started.json()["id"])
        run_id = job["result"]["run_id"]
        first_global = await client.get("/api/v1/updates")
        first_run = await client.get(f"/api/v1/runs/{run_id}/updates")

        identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.40001v1",
                title="Reliable agent paper reading with follow-up",
                authors=("Alice Researcher", "Bob Researcher"),
                abstract="Expanded evaluation and evidence.",
                doi="10.1000/reader-update",
                updated_at=datetime(2026, 7, 15, tzinfo=UTC),
            ),
            now=NOW + timedelta(days=1),
        )
        later_global = await client.get("/api/v1/updates")
        later_run = await client.get(f"/api/v1/runs/{run_id}/updates")

    assert started.status_code == 202
    assert job["status"] == "succeeded"
    assert first_global.json()["total"] == 1
    assert first_run.status_code == 200
    assert first_run.json()["total"] == 1
    attributed_id = first_run.json()["items"][0]["id"]
    assert later_global.json()["total"] == 2
    assert later_run.json()["total"] == 1
    assert later_run.json()["items"][0]["id"] == attributed_id
