from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.recommendation.test_result_api import (
    bootstrap,
    local_client,
    recommendation_app,
    seed_recommendation,
)


@pytest.mark.asyncio
async def test_briefing_has_an_explicit_first_run_empty_state(tmp_path: Path) -> None:
    app = recommendation_app(tmp_path)

    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get("/api/v1/briefing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["empty_state"]["active"] is True
    assert payload["empty_state"]["reason"] == "no_discovery_plans"
    assert payload["plans"] == []
    assert payload["new_papers"] == []
    assert payload["important_revisions"] == []
    assert payload["pending_catchups"] == []
    assert payload["exploration_hint"] is None

    schema = app.openapi()["components"]["schemas"]["ReadingBriefingResponse"]
    assert "exploration_hint" in schema["properties"]
    assert "exploration_hint" not in schema["required"]


@pytest.mark.asyncio
async def test_briefing_combines_plans_papers_revisions_catchups_and_job_attention(
    tmp_path: Path,
) -> None:
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import (
        PaperUpdateRecord,
        PlanCatchupRecord,
        SourceRevisionRecord,
    )
    from paper_reader.jobs.models import JobRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.models import LibraryEntryRecord

    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)
    first = seeded["ingested"][0]
    revised_at = datetime(2026, 7, 15, 8, tzinfo=UTC)
    revision = IdentityRepository(
        app.state.sessions,
        work_index=app.state.lexical_index,
    ).ingest(
        SourcePaper(
            connector_id="arxiv",
            external_id="2607.10001v1",
            title="Reliable paper agents with grounded evidence",
            authors=("Alice Researcher",),
            abstract="Expanded evaluation, limitations, and ablation evidence.",
            updated_at=revised_at,
        ),
        now=revised_at,
        run_id=seeded["run"].id,
    )
    assert revision.paper_update_id is not None

    with app.state.sessions() as session:
        session.add(
            PlanCatchupRecord(
                plan_id=seeded["plan"].id,
                scheduled_for=revised_at - timedelta(hours=2),
                created_at=revised_at,
            )
        )
        session.commit()

    jobs = JobRepository(app.state.sessions)
    active = jobs.create(kind="fixture.active", payload={"work_id": first.work.id})
    jobs.update_state(active.id, status="running", progress=40)
    failed = jobs.create(kind="metadata.enrichment", payload={"run_id": seeded["run"].id})
    jobs.update_state(
        failed.id,
        status="failed",
        progress=60,
        error_code="source_rate_limited",
        error_message="Metadata source rate limited",
    )
    with app.state.sessions() as session:
        # Older unread revisions and unfinished jobs must survive newer unrelated activity.
        unread = session.get(PaperUpdateRecord, revision.paper_update_id)
        assert unread is not None
        for index in range(101):
            observed = revised_at + timedelta(minutes=index + 1)
            revision_id = f"read-revision-{index}"
            session.add(
                SourceRevisionRecord(
                    id=revision_id,
                    source_record_id=unread.source_record_id,
                    work_id=unread.work_id,
                    revision_kind="important_revision",
                    payload_sha256=f"{index:064x}",
                    snapshot={},
                    fetched_at=observed,
                )
            )
        session.flush()
        for index in range(101):
            session.add(
                PaperUpdateRecord(
                    source_record_id=unread.source_record_id,
                    work_id=unread.work_id,
                    previous_revision_id=unread.current_revision_id,
                    current_revision_id=f"read-revision-{index}",
                    update_type="important_revision",
                    visibility="updates",
                    state="read",
                    detected_at=revised_at + timedelta(minutes=index + 1),
                )
            )
            session.add(
                JobRecord(
                    kind="fixture.complete",
                    status="succeeded",
                    created_at=failed.created_at + timedelta(minutes=index + 1),
                )
            )
        session.add(
            LibraryEntryRecord(
                work_id=seeded["ingested"][1].work.id,
                added_via="save",
            )
        )
        session.commit()

    async with local_client(app) as client:
        await bootstrap(client)
        response = await client.get("/api/v1/briefing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["empty_state"]["active"] is False
    assert payload["plans"][0]["id"] == seeded["plan"].id
    assert payload["plans"][0]["latest_run"]["id"] == seeded["run"].id
    assert payload["plans"][0]["latest_run"]["recommendation_count"] == 2
    assert {item["work_id"] for item in payload["new_papers"]} == {
        seeded["ingested"][0].work.id,
        seeded["ingested"][1].work.id,
    }
    assert payload["important_revisions"][0]["id"] == revision.paper_update_id
    assert payload["important_revisions"][0]["state"] == "unread"
    assert payload["pending_catchups"][0]["plan_id"] == seeded["plan"].id
    assert any(item["id"] == active.id for item in payload["active_jobs"])
    assert any(
        item["id"] == failed.id and item["error_code"] == "source_rate_limited"
        for item in payload["failed_jobs"]
    )
    hint = payload["exploration_hint"]
    assert hint["work_id"] == seeded["ingested"][0].work.id
    assert hint["related_work_ids"] == [seeded["ingested"][1].work.id]
    assert 1 <= len(hint["suggested_terms"]) <= 5
    assert "本地论文库" in hint["reason"]


def test_briefing_uses_only_the_latest_run_and_its_latest_result_set(tmp_path: Path) -> None:
    from paper_reader.discovery.runs import DiscoveryRunRepository, RunBatchSpec
    from paper_reader.recommendation.briefing import BriefingService
    from paper_reader.recommendation.results import RecommendationSetRepository

    app = recommendation_app(tmp_path)
    seeded = seed_recommendation(app)
    sessions = app.state.sessions
    run = seeded["run"]
    newer_set = RecommendationSetRepository(sessions).create(
        run_id=run.id,
        candidates=seeded["candidates"],
        snapshot_refs=seeded["refs"],
        fixed_historical_months=False,
        limit=1,
        candidate_cap=5,
    )
    service = BriefingService(sessions)
    first = service.build()
    assert first.plans[0].latest_run.recommendation_set_id == newer_set.id
    assert [paper.recommendation_set_id for paper in first.new_papers] == [newer_set.id]

    newer_run = DiscoveryRunRepository(sessions).create_with_job(
        plan_id=run.plan_id,
        keyword_version_id=run.keyword_version_id,
        months=run.months,
        plan_snapshot=run.plan_snapshot,
        keyword_snapshot=run.keyword_snapshot,
        connector_versions=run.connector_versions,
        batches=(RunBatchSpec("2026-07", "arxiv", "b" * 64),),
        job_payload={},
        idempotency_key=None,
        created_at=run.created_at + timedelta(seconds=1),
    )
    second = service.build()
    assert second.plans[0].latest_run.id == newer_run.id
    assert second.plans[0].latest_run.recommendation_set_id is None
    assert second.new_papers == ()
    sessions.kw["bind"].dispose()
