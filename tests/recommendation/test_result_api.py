from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_reader.settings import AppPaths

NOW = datetime(2026, 7, 15, 6, tzinfo=UTC)


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


def recommendation_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.app import create_app

    return create_app(
        paths=AppPaths.from_overrides(
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "library",
        ),
        bootstrap_token="secret",
    )


def seed_recommendation(app: Any) -> dict[str, Any]:
    from paper_reader.discovery.identity import (
        DiscoveryKind,
        IdentityRepository,
        MatchKind,
        SourcePaper,
    )
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        DiscoveryPlanRepository,
        PlanQuota,
    )
    from paper_reader.discovery.runs import (
        CandidateIngest,
        DiscoveryRunRepository,
        RunBatchSpec,
    )
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.recommendation.preferences import (
        AnalysisState,
        LibraryFeedback,
        LibraryStateInput,
        PreferenceRepository,
    )
    from paper_reader.recommendation.results import RecommendationSetRepository
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository
    from tests.recommendation.test_ranking import candidate
    from tests.recommendation.test_recommendation_results import _snapshot_refs

    keywords = KeywordRepository(app.state.sessions)
    keyword_version = keywords.create_version(
        research_intent="Track reliable paper-reading agents",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="agent",
                    label="paper agent",
                    terms=("paper agent", "literature agent"),
                ),
            ),
        ),
        generator_info={"backend": "fixture", "intent_used_for_filtering": False},
    )
    keywords.confirm(keyword_version.id)
    plan = DiscoveryPlanRepository(app.state.sessions).create(
        DiscoveryPlanCreate(
            name="Paper agent watch",
            research_intent="Track reliable paper-reading agents",
            keyword_version_id=keyword_version.id,
            date_range=DateRangeSpec(mode="rolling", rolling_months=1),
            sources=("arxiv",),
            quota=PlanQuota(priority_limit=2, candidate_limit=5),
        ),
        now=NOW,
    )
    runs = DiscoveryRunRepository(app.state.sessions)
    run = runs.create_with_job(
        plan_id=plan.id,
        keyword_version_id=keyword_version.id,
        months=("2026-07",),
        plan_snapshot={
            "name": plan.name,
            "date_range": {"mode": "rolling", "rolling_months": 1},
            "quota": {"priority_limit": 2, "candidate_limit": 5},
        },
        keyword_snapshot={
            "version_id": keyword_version.id,
            "definition": keyword_version.definition.model_dump(mode="json"),
        },
        connector_versions={"arxiv": "fixture-v1"},
        batches=(
            RunBatchSpec(
                month="2026-07",
                connector_id="arxiv",
                query_fingerprint="a" * 64,
            ),
        ),
        job_payload={"plan_id": plan.id},
        idempotency_key="result-api-discovery",
        created_at=NOW,
    )
    identities = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    )
    source_inputs = (
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10001v1",
            title="Reliable paper agents with grounded evidence",
            authors=("Alice Researcher",),
            abstract="Initial evidence for grounded paper agents.",
            updated_at=datetime(2026, 7, 10, tzinfo=UTC),
        ),
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10002v1",
            title="High-quality literature agents for systematic review",
            authors=("Bob Researcher",),
            abstract="Systematic review agent evaluation.",
            updated_at=datetime(2026, 7, 14, tzinfo=UTC),
        ),
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10003v1",
            title="A previously saved paper-reading agent",
            authors=("Carol Researcher",),
            abstract="A historical library match.",
            updated_at=datetime(2026, 7, 12, tzinfo=UTC),
        ),
    )
    ingested = tuple(identities.ingest(item, now=NOW) for item in source_inputs)
    runs.record_candidates(
        run_id=run.id,
        batch_id=run.batches[0].id,
        candidates=tuple(
            CandidateIngest(
                work_id=item.work.id,
                source_record_id=item.source_record.id,
                match_kind=MatchKind.NEW,
                discovery_kind=DiscoveryKind.NEW_WORK,
            )
            for item in ingested
        ),
        now=NOW,
    )
    runs.succeed_batch(run.batches[0].id, now=NOW)
    run = runs.finish_run(run.id, status="succeeded", now=NOW)
    JobRepository(app.state.sessions).update_state(
        run.job_id,
        status="succeeded",
        progress=100,
        result={"run_id": run.id},
    )

    high_relevance, high_quality, historical = (item.work for item in ingested)
    candidates = (
        candidate(
            high_relevance.id,
            relevance=96,
            quality=82,
            freshness=70,
            preference=80,
            updated_at=source_inputs[0].updated_at,
        ),
        candidate(
            high_quality.id,
            relevance=70,
            quality=98,
            freshness=90,
            preference=50,
            updated_at=source_inputs[1].updated_at,
        ),
        candidate(
            historical.id,
            relevance=88,
            quality=88,
            freshness=80,
            preference=95,
            updated_at=source_inputs[2].updated_at,
            historical_match=True,
        ),
    )
    snapshots = ScoreSnapshotRepository(app.state.sessions)
    refs = {
        item.work_id: _snapshot_refs(snapshots, run.id, item)
        for item in candidates
    }
    result = RecommendationSetRepository(app.state.sessions).create(
        run_id=run.id,
        candidates=candidates,
        snapshot_refs=refs,
        fixed_historical_months=False,
        limit=2,
        candidate_cap=5,
    )
    PreferenceRepository(app.state.sessions).set_library_state(
        high_relevance.id,
        LibraryStateInput(
            favorite=True,
            feedback=LibraryFeedback.LIKE,
            feedback_reasons=("method_matches_interest",),
            pinned=False,
            reading_progress=35,
            analysis_state=AnalysisState.NOT_ANALYZED,
        ),
    )
    return {
        "plan": plan,
        "run": run,
        "result": result,
        "ingested": ingested,
        "candidates": candidates,
        "refs": refs,
    }


@pytest.mark.asyncio
async def test_recommendation_result_api_exposes_five_views_and_compact_evidence(
    tmp_path: Path,
) -> None:
    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)

    async with local_client(app) as client:
        await bootstrap(client)
        composite = await client.get(
            f"/api/v1/runs/{seeded['run'].id}/recommendations?view=composite"
        )
        quality = await client.get(
            f"/api/v1/runs/{seeded['run'].id}/recommendations?view=quality"
        )
        invalid = await client.get(
            f"/api/v1/runs/{seeded['run'].id}/recommendations?view=agent_opinion"
        )

    assert composite.status_code == 200
    payload = composite.json()
    assert payload["run_id"] == seeded["run"].id
    assert payload["recommendation_set_id"] == seeded["result"].id
    assert payload["view"] == "composite"
    assert payload["available_views"] == [
        "composite",
        "relevance",
        "quality",
        "newest",
        "preference",
    ]
    assert len(payload["items"]) == 2
    assert len(payload["historical_matches"]) == 1
    first = payload["items"][0]
    assert first["title"]
    assert first["authors"]
    assert set(first["scores"]) == {
        "relevance",
        "quality",
        "freshness",
        "preference",
        "composite",
    }
    assert all(
        set(value) == {
            "snapshot_id",
            "rule_version",
            "score",
            "coverage",
            "confidence",
        }
        for value in first["scores"].values()
    )
    assert "components" not in first["evidence_summary"]
    assert first["library_state"]["favorite"] is True
    assert quality.status_code == 200
    assert quality.json()["items"][0]["work_id"] == seeded["ingested"][1].work.id
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_score_snapshot_detail_is_separate_and_missing_results_are_explicit(
    tmp_path: Path,
) -> None:
    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)
    first_item = seeded["result"].items[0]

    async with local_client(app) as client:
        await bootstrap(client)
        detail = await client.get(
            "/api/v1/scores/snapshots/"
            f"{first_item.snapshots.quality_snapshot_id}"
        )
        missing_run = await client.get(
            "/api/v1/runs/missing-run/recommendations"
        )

    assert detail.status_code == 200
    assert detail.json()["score_type"] == "quality"
    assert detail.json()["components"]
    assert len(detail.json()["snapshot_sha256"]) == 64
    assert missing_run.status_code == 404


@pytest.mark.asyncio
async def test_manual_recommendation_refresh_is_idempotent_and_rejects_agent_options(
    tmp_path: Path,
) -> None:
    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)

    async with local_client(app) as client:
        headers = await bootstrap(client)
        rejected = await client.post(
            f"/api/v1/runs/{seeded['run'].id}/recommendations/refresh",
            json={"model": "gpt-does-not-belong-in-ranking"},
            headers=headers,
        )
        first = await client.post(
            f"/api/v1/runs/{seeded['run'].id}/recommendations/refresh",
            json={},
            headers={**headers, "Idempotency-Key": "manual-refresh-once"},
        )
        repeated = await client.post(
            f"/api/v1/runs/{seeded['run'].id}/recommendations/refresh",
            json={},
            headers={**headers, "Idempotency-Key": "manual-refresh-once"},
        )

    assert rejected.status_code == 422
    assert first.status_code == 202
    assert repeated.status_code == 202
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["kind"] == "recommendation.run"


@pytest.mark.asyncio
async def test_recommendation_reads_require_session_and_refresh_requires_csrf(
    tmp_path: Path,
) -> None:
    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)
    snapshot_id = seeded["result"].items[0].snapshots.composite_snapshot_id

    async with local_client(app) as client:
        result_without_session = await client.get(
            f"/api/v1/runs/{seeded['run'].id}/recommendations"
        )
        briefing_without_session = await client.get("/api/v1/briefing")
        snapshot_without_session = await client.get(
            f"/api/v1/scores/snapshots/{snapshot_id}"
        )
        await bootstrap(client)
        refresh_without_csrf = await client.post(
            f"/api/v1/runs/{seeded['run'].id}/recommendations/refresh",
            json={},
            headers={"Origin": "http://127.0.0.1"},
        )

    assert result_without_session.status_code == 401
    assert briefing_without_session.status_code == 401
    assert snapshot_without_session.status_code == 401
    assert refresh_without_csrf.status_code == 403


@pytest.mark.asyncio
async def test_result_preserves_scored_identity_but_resolves_current_merged_work(
    tmp_path: Path,
) -> None:
    from sqlalchemy import update

    from paper_reader.discovery.models import (
        LibraryStateRecord,
        SourceRecord,
        WorkRecord,
    )

    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)
    scored_work_id = seeded["ingested"][0].work.id
    with app.state.sessions() as session:
        target = WorkRecord(
            canonical_title="Canonical grounded paper agent",
            normalized_title="canonical grounded paper agent",
            authors=["Alice Researcher"],
            abstract="Merged canonical record.",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(target)
        session.flush()
        session.execute(
            update(SourceRecord)
            .where(SourceRecord.work_id == scored_work_id)
            .values(work_id=target.id)
        )
        library_state = session.get(LibraryStateRecord, scored_work_id)
        assert library_state is not None
        library_state.work_id = target.id
        scored_work = session.get(WorkRecord, scored_work_id)
        assert scored_work is not None
        scored_work.status = "merged"
        scored_work.merged_into_work_id = target.id
        session.commit()
        canonical_id = target.id

    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get(
            f"/api/v1/runs/{seeded['run'].id}/recommendations"
        )

    assert response.status_code == 200
    item = next(
        value
        for value in response.json()["items"]
        if value["scored_work_id"] == scored_work_id
    )
    assert item["work_id"] == canonical_id
    assert item["title"] == "Canonical grounded paper agent"
    assert item["library_state"]["work_id"] == canonical_id
    assert item["library_state"]["favorite"] is True


def test_presentation_query_count_is_bounded_per_batch(tmp_path: Path) -> None:
    from dataclasses import replace

    from sqlalchemy import event

    from paper_reader.discovery.models import WorkRecord
    from paper_reader.recommendation.result_api import RecommendationResultReader

    app = recommendation_app(tmp_path)
    seed = seed_recommendation(app)
    sessions = app.state.sessions
    items = tuple(
        replace(seed["result"].items[0], scored_work_id=f"work-{index}")
        for index in range(100)
    )
    with sessions.begin() as session:
        session.add_all(
            WorkRecord(id=item.scored_work_id, canonical_title="Sample", normalized_title="sample")
            for item in items
        )
    reader = RecommendationResultReader(sessions)
    statements: list[str] = []

    def record(_conn, _cursor, sql, *_args):  # type: ignore[no-untyped-def]
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        for count in (1, 20, 100):
            statements.clear()
            assert len(reader.present(items[:count])) == count
            assert len(statements) == 3
    finally:
        event.remove(engine, "before_cursor_execute", record)
        engine.dispose()
