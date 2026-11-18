from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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


@pytest.mark.asyncio
async def test_plan_api_creates_month_plan_from_confirmed_keywords(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="secret")
    keywords = KeywordRepository(app.state.sessions)
    version = keywords.create_version(
        research_intent="agent research",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="agent",
                    label="智能体",
                    terms=("AI agent", "人工智能智能体"),
                ),
            )
        ),
    )
    keywords.confirm(version.id)

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        created = await client.post(
            "/api/v1/plans",
            json={
                "name": "智能体追踪",
                "research_intent": "agent research",
                "keyword_version_id": version.id,
                "date_range": {
                    "mode": "fixed",
                    "start_month": "2024-01",
                    "end_month": "2026-02",
                    "timezone": "Asia/Shanghai",
                },
                "sources": ["arxiv", "openreview"],
                "source_filters": {"arxiv": {"categories": ["cs.AI"]}},
            },
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )
        plans = await client.get("/api/v1/plans")

    assert created.status_code == 201
    assert created.json()["warnings"] == ["date_range_over_24_months"]
    assert created.json()["sources"] == ["arxiv", "openreview"]
    assert plans.status_code == 200
    assert [item["id"] for item in plans.json()] == [created.json()["id"]]


@pytest.mark.asyncio
async def test_application_open_collects_but_does_not_run_missed_plan(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.connectors import ConnectorRegistry
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        DiscoveryPlanRepository,
        ScheduleSpec,
    )

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(
        paths=paths,
        bootstrap_token="secret",
        connector_registry=ConnectorRegistry(()),
    )
    keywords = KeywordRepository(app.state.sessions)
    version = keywords.create_version(
        research_intent="agent research",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="agent",
                    label="智能体",
                    terms=("AI agent", "人工智能智能体"),
                ),
            )
        ),
    )
    keywords.confirm(version.id)
    DiscoveryPlanRepository(app.state.sessions).create(
        DiscoveryPlanCreate(
            name="错过任务测试",
            research_intent="agent research",
            keyword_version_id=version.id,
            date_range=DateRangeSpec(mode="rolling", rolling_months=1),
            sources=("arxiv",),
            schedule=ScheduleSpec(frequency="daily", local_time="00:00"),
        ),
        now=datetime.now(UTC) - timedelta(days=2),
    )

    async with local_client(app) as client:
        await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "secret"},
            headers={"Origin": "http://127.0.0.1"},
        )
        csrf = client.cookies["paper_reader_csrf"]
        pending = await client.get("/api/v1/plans/catchups")
        first_id = pending.json()[0]["id"]
        confirmed = await client.post(
            f"/api/v1/plans/catchups/{first_id}/confirm",
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )
        duplicate = await client.post(
            f"/api/v1/plans/catchups/{first_id}/confirm",
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": csrf,
            },
        )
        remaining = await client.get("/api/v1/plans/catchups")

    assert pending.status_code == 200
    assert len(pending.json()) >= 1
    assert all(item["status"] == "pending" for item in pending.json())
    assert confirmed.status_code == 202
    assert confirmed.json()["kind"] == "discovery.run"
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == confirmed.json()["id"]
    assert len(remaining.json()) == len(pending.json()) - 1
