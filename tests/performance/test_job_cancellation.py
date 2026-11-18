from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from statistics import median
from typing import Any

import httpx
import pytest

CANCEL_ACKNOWLEDGEMENT_BUDGET_SECONDS = 2.0
CANCEL_SAMPLE_COUNT = 20


@asynccontextmanager
async def _local_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        yield client


def test_cancel_request_is_persisted_and_event_visible_within_budget(tmp_path: Path) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "cancel-performance.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    repository = JobRepository(create_session_factory(engine))
    try:
        created = repository.create(
            kind="performance.cooperative",
            payload={"fixture": True},
            idempotency_key="cancel-performance",
        )
        repository.update_state(created.id, status="running", progress=37)

        started = time.perf_counter()
        acknowledged = repository.request_cancel(created.id)
        events = [
            event
            for event in repository.list_events(after_event_id=0)
            if event.job_id == created.id
        ]
        elapsed = time.perf_counter() - started

        assert elapsed <= CANCEL_ACKNOWLEDGEMENT_BUDGET_SECONDS
        assert acknowledged is not None
        assert acknowledged.status == "running"
        assert acknowledged.progress == 37
        assert acknowledged.cancel_requested is True
        assert [(event.event_type, event.progress, event.payload) for event in events] == [
            ("job.cancel_requested", 37, {}),
        ]

        repeated = repository.request_cancel(created.id)
        replayed_events = [
            event
            for event in repository.list_events(after_event_id=0)
            if event.job_id == created.id
        ]
        assert repeated is not None and repeated.cancel_requested is True
        assert [event.event_id for event in replayed_events] == [events[0].event_id]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_cancel_api_response_and_resumable_event_are_visible_within_budget(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.settings import AppPaths

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    app = create_app(paths=paths, bootstrap_token="cancel-performance-token")
    repository = JobRepository(app.state.sessions)
    jobs = []
    for sample in range(CANCEL_SAMPLE_COUNT):
        created = repository.create(
            kind="performance.api",
            payload={"sample": sample},
            idempotency_key=f"cancel-api-performance-{sample}",
        )
        jobs.append(repository.update_state(created.id, status="running", progress=19))

    async with _local_client(app) as client:
        bootstrap = await client.post(
            "/api/v1/session/bootstrap",
            json={"token": "cancel-performance-token"},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert bootstrap.status_code == 204
        csrf_token = client.cookies["paper_reader_csrf"]

        samples: list[float] = []
        last_event_id = 0
        for created in jobs:
            started = time.perf_counter()
            response = await client.post(
                f"/api/v1/jobs/{created.id}/cancel",
                headers={
                    "Origin": "http://127.0.0.1",
                    "X-CSRF-Token": csrf_token,
                },
            )
            events_response = await client.get(
                f"/api/v1/events/poll?after_event_id={last_event_id}"
            )
            samples.append((time.perf_counter() - started) * 1_000)

            assert response.status_code == 200
            assert response.json()["cancel_requested"] is True
            assert events_response.status_code == 200
            events = [
                event
                for event in events_response.json()
                if event["job_id"] == created.id
                and event["type"] == "job.cancel_requested"
            ]
            assert len(events) == 1
            assert events[0]["progress"] == 19
            last_event_id = max(event["event_id"] for event in events_response.json())

    ordered = sorted(samples)
    p95 = ordered[-2]
    evidence = {
        "schema_version": "paper-reader-cancellation-performance-v1",
        "samples_ms": [round(value, 3) for value in samples],
        "min_ms": round(ordered[0], 3),
        "median_ms": round(median(ordered), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(ordered[-1], 3),
        "budget_ms": CANCEL_ACKNOWLEDGEMENT_BUDGET_SECONDS * 1_000,
    }
    print(f"PAPER_READER_CANCELLATION_PERFORMANCE {json.dumps(evidence)}")
    assert p95 <= CANCEL_ACKNOWLEDGEMENT_BUDGET_SECONDS * 1_000
