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


def preference_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper

    app = create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )
    work = IdentityRepository(app.state.sessions).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.12345",
            title="Agentic Reading for Diffusion Research",
            authors=("Alice Chen", "Bob Li"),
        )
    ).work
    return app, work


@pytest.mark.asyncio
async def test_library_api_updates_independent_state_and_exposes_preference_score(
    tmp_path: Path,
) -> None:
    app, work = preference_app(tmp_path)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        empty = await client.get(f"/api/v1/library/works/{work.id}")
        saved = await client.put(
            f"/api/v1/library/works/{work.id}",
            json={
                "favorite": True,
                "feedback": "like",
                "feedback_reasons": ["method_matches_interest"],
                "pinned": False,
                "reading_progress": 35,
                "analysis_state": "available",
            },
            headers=headers,
        )
        author = await client.put(
            "/api/v1/library/interested-authors/alice%20chen",
            json={"display_name": "Alice Chen", "active": True},
            headers=headers,
        )
        score = await client.get(
            f"/api/v1/library/works/{work.id}/preference-score"
        )

    assert empty.status_code == 200
    assert empty.json()["persisted"] is False
    assert saved.status_code == 200
    assert saved.json()["historical_match"] is True
    assert saved.json()["analysis_state"] == "available"
    assert author.status_code == 200
    assert score.status_code == 200
    assert score.json()["rule_version"] == "preference-v1"
    assert score.json()["matched_author_keys"] == ["alice chen"]
    assert score.json()["suppressed_by_default"] is False


@pytest.mark.asyncio
async def test_implicit_event_api_is_whitelisted_server_timed_and_idempotent(
    tmp_path: Path,
) -> None:
    app, work = preference_app(tmp_path)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        payload = {"event_key": "detail:session-1", "event_type": "detail_open"}
        first = await client.post(
            f"/api/v1/library/works/{work.id}/events",
            json=payload,
            headers=headers,
        )
        repeated = await client.post(
            f"/api/v1/library/works/{work.id}/events",
            json=payload,
            headers=headers,
        )
        fabricated_time = await client.post(
            f"/api/v1/library/works/{work.id}/events",
            json={
                **payload,
                "event_key": "fabricated",
                "occurred_at": "2000-01-01T00:00:00Z",
            },
            headers=headers,
        )
        unapproved = await client.post(
            f"/api/v1/library/works/{work.id}/events",
            json={"event_key": "keyboard", "event_type": "keyboard_event"},
            headers=headers,
        )
        score = await client.get(
            f"/api/v1/library/works/{work.id}/preference-score"
        )

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert first.json()["weight"] == 0.5
    assert fabricated_time.status_code == 422
    assert unapproved.status_code == 422
    assert score.json()["behavior_boost"] == 0.5


@pytest.mark.asyncio
async def test_library_mutations_require_csrf_and_missing_works_are_not_created(
    tmp_path: Path,
) -> None:
    app, work = preference_app(tmp_path)

    async with local_client(app) as client:
        await bootstrap(client)
        payload = {
            "favorite": False,
            "feedback": "dislike",
            "feedback_reasons": ["wrong_scope"],
            "pinned": False,
            "reading_progress": 0,
            "analysis_state": "not_analyzed",
        }
        missing_csrf = await client.put(
            f"/api/v1/library/works/{work.id}",
            json=payload,
            headers={"Origin": "http://127.0.0.1"},
        )
        missing_work = await client.get("/api/v1/library/works/not-a-work")

    assert missing_csrf.status_code == 403
    assert missing_work.status_code == 404


@pytest.mark.asyncio
async def test_application_startup_enforces_raw_event_retention(tmp_path: Path) -> None:
    from paper_reader.recommendation.preferences import (
        ImplicitEventInput,
        ImplicitEventType,
        PreferenceRepository,
    )

    app, work = preference_app(tmp_path)
    repository = PreferenceRepository(app.state.sessions)
    repository.record_implicit_event(
        work.id,
        ImplicitEventInput(
            event_key="expired-before-start",
            event_type=ImplicitEventType.DETAIL_OPEN,
            occurred_at=datetime.now(UTC) - timedelta(days=181),
        ),
    )

    async with local_client(app):
        assert repository.list_raw_events(work.id) == ()
        assert repository.get_aggregated_weight(work.id) == 0.5
