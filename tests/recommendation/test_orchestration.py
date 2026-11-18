from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


class FixtureRelevance:
    def __init__(
        self,
        run_id: str,
        keyword_version_id: str,
        works: tuple[object, ...],
    ) -> None:
        self.run_id = run_id
        self.keyword_version_id = keyword_version_id
        self.works = works
        self.calls = 0
        self.fail_if_called = False

    def score_run(self, run_id: str):  # type: ignore[no-untyped-def]
        from paper_reader.recommendation.relevance import (
            KeywordAlignment,
            MetricEvidence,
            score_relevance,
        )
        from paper_reader.recommendation.service import (
            CandidateRelevance,
            RunRelevanceResult,
        )

        if self.fail_if_called:
            raise AssertionError("prepared recommendation facts were recomputed")
        assert run_id == self.run_id
        self.calls += 1
        candidates = []
        for index, work in enumerate(self.works):
            relevance = score_relevance(
                lexical=MetricEvidence.observed(90 - (index * 10)),
                semantic=MetricEvidence.unknown(reason="embedding_model_not_installed"),
                keyword=MetricEvidence.observed(100),
            )
            candidates.append(
                CandidateRelevance(
                    work_id=work.id,
                    title=work.canonical_title,
                    authors=tuple(work.authors),
                    published_at=work.published_at,
                    updated_at=datetime(2026, 7, 14 - index, tzinfo=UTC),
                    source_ids=("arxiv",) if index == 0 else ("openreview",),
                    eligible=True,
                    lexical_hit=None,
                    semantic_hit=None,
                    keyword_alignment=KeywordAlignment(
                        score=100,
                        eligible=True,
                        required_matches={"agent": "paper agent"},
                        boost_matches={},
                        excluded_matches={},
                    ),
                    relevance=relevance,
                )
            )
        return RunRelevanceResult(
            run_id=run_id,
            keyword_version_id=self.keyword_version_id,
            query="paper agent",
            candidates=tuple(candidates),
        )


class FailOnceSnapshotRepository:
    def __init__(self, delegate, failing_work_id: str) -> None:  # type: ignore[no-untyped-def]
        self.delegate = delegate
        self.failing_work_id = failing_work_id
        self.armed = False
        self.failed = False

    def record(self, **kwargs):  # type: ignore[no-untyped-def]
        if (
            self.armed
            and not self.failed
            and kwargs["work_id"] == self.failing_work_id
            and kwargs["score_type"] == "relevance"
        ):
            self.failed = True
            raise RuntimeError("simulated score persistence failure")
        return self.delegate.record(**kwargs)


@pytest.fixture
def orchestration_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )
    from paper_reader.discovery.models import (
        PlanRunRecord,
        RunBatchRecord,
        RunCandidateRecord,
    )
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        DiscoveryPlanRepository,
        PlanQuota,
    )
    from paper_reader.discovery.runs import DiscoveryRunRepository, RunBatchSpec
    from paper_reader.jobs.models import JobRecord
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.evidence_builder import QualityEvidenceBuilder
    from paper_reader.recommendation.observations import MetadataObservationRepository
    from paper_reader.recommendation.preferences import PreferenceRepository
    from paper_reader.recommendation.rating_catalogs import RatingCatalogRepository
    from paper_reader.recommendation.results import RecommendationSetRepository
    from paper_reader.recommendation.score_rules import ScoreRuleRepository
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository
    from paper_reader.recommendation.venue_rating import VenueRatingService

    database = tmp_path / "recommendation-orchestration.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    now = datetime(2026, 7, 15, 8, tzinfo=UTC)

    keywords = KeywordRepository(sessions)
    keyword_version = keywords.create_version(
        research_intent="reliable paper agents",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="agent",
                    label="Paper agent",
                    terms=("paper agent",),
                ),
            ),
        ),
        generator_info={"backend": "fixture", "intent_used_for_filtering": False},
    )
    keywords.confirm(keyword_version.id)
    plan = DiscoveryPlanRepository(sessions).create(
        DiscoveryPlanCreate(
            name="Fixed agent reading",
            research_intent="reliable paper agents",
            keyword_version_id=keyword_version.id,
            date_range=DateRangeSpec(
                mode="fixed",
                start_month="2026-07",
                end_month="2026-07",
            ),
            sources=("arxiv", "openreview"),
            quota=PlanQuota(priority_limit=2, candidate_limit=5),
        ),
        now=now,
    )
    runs = DiscoveryRunRepository(sessions)
    run = runs.create_with_job(
        plan_id=plan.id,
        keyword_version_id=keyword_version.id,
        months=("2026-07",),
        plan_snapshot={
            "date_range": plan.date_range.model_dump(mode="json"),
            "quota": {"priority_limit": 2, "candidate_limit": 5},
            "sources": list(plan.sources),
        },
        keyword_snapshot={
            "version_id": keyword_version.id,
            "definition": keyword_version.definition.model_dump(mode="json"),
        },
        connector_versions={"arxiv": "fixture-v1", "openreview": "fixture-v1"},
        batches=(
            RunBatchSpec("2026-07", "arxiv", "a" * 64),
            RunBatchSpec("2026-07", "openreview", "b" * 64),
        ),
        job_payload={"plan_id": plan.id},
        idempotency_key="orchestration-discovery-run",
        created_at=now,
    )
    identities = IdentityRepository(sessions)
    ingested = (
        identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2607.00001v1",
                title="Reliable Paper Agent",
                authors=("Alice",),
                published_at=now - timedelta(days=7),
                updated_at=now - timedelta(days=1),
            )
        ),
        identities.ingest(
            SourcePaper(
                connector_id="openreview",
                external_id="forum-2",
                title="Diverse Paper Agent",
                authors=("Bob",),
                published_at=now - timedelta(days=14),
                updated_at=now - timedelta(days=2),
            )
        ),
    )
    works = tuple(item.work for item in ingested)
    with sessions() as session:
        run_record = session.get(PlanRunRecord, run.id)
        assert run_record is not None
        run_record.status = "succeeded"
        run_record.succeeded_batches = 2
        run_record.completed_at = now
        discovery_job = session.get(JobRecord, run.job_id)
        assert discovery_job is not None
        discovery_job.status = "succeeded"
        discovery_job.progress = 100
        batches = tuple(
            session.query(RunBatchRecord)
            .filter(RunBatchRecord.run_id == run.id)
            .order_by(RunBatchRecord.connector_id)
        )
        for batch in batches:
            batch.status = "succeeded"
            batch.completed_at = now
        batch_by_source = {batch.connector_id: batch for batch in batches}
        for item in ingested:
            session.add(
                RunCandidateRecord(
                    run_id=run.id,
                    batch_id=batch_by_source[item.source_record.connector_id].id,
                    work_id=item.work.id,
                    source_record_id=item.source_record.id,
                    match_kind=item.match_kind.value,
                    discovery_kind=item.discovery_kind.value,
                    created_at=now,
                )
            )
        session.commit()

    snapshots = ScoreSnapshotRepository(sessions)
    relevance = FixtureRelevance(run.id, run.keyword_version_id, works)
    context = {
        "sessions": sessions,
        "run_id": run.id,
        "works": works,
        "runs": runs,
        "jobs": JobRepository(sessions),
        "relevance": relevance,
        "quality": QualityEvidenceBuilder(
            sessions=sessions,
            observations=MetadataObservationRepository(sessions),
            venue_ratings=VenueRatingService(
                RatingCatalogRepository(sessions),
            ),
        ),
        "preferences": PreferenceRepository(sessions),
        "rules": ScoreRuleRepository(sessions),
        "snapshots": snapshots,
        "results": RecommendationSetRepository(sessions),
        "clock": lambda: now,
    }
    try:
        yield context
    finally:
        engine.dispose()


def _service(context, *, snapshots=None):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.orchestration import RecommendationOrchestrator

    return RecommendationOrchestrator(
        runs=context["runs"],
        jobs=context["jobs"],
        relevance=context["relevance"],
        quality=context["quality"],
        preferences=context["preferences"],
        rules=context["rules"],
        snapshots=snapshots or context["snapshots"],
        results=context["results"],
        clock=context["clock"],
    )


@pytest.mark.asyncio
async def test_fixed_month_run_persists_reproducible_five_signal_result_set(
    orchestration_context,
) -> None:  # type: ignore[no-untyped-def]
    service = _service(orchestration_context)
    job = service.create_job(
        orchestration_context["run_id"],
        trigger="discovery",
        trigger_job_id="discovery-job",
    )

    completed = await service.execute(job.id)

    assert completed.status == "succeeded"
    assert completed.result is not None
    result = orchestration_context["results"].latest_for_run(
        orchestration_context["run_id"]
    )
    assert result is not None
    assert result.id == completed.result["recommendation_set_id"]
    assert result.fixed_historical_months is True
    assert result.recommendation_count == 2
    assert orchestration_context["relevance"].calls == 1
    for item in result.items:
        composite = orchestration_context["snapshots"].latest(
            item.scored_work_id,
            score_type="composite",
            context_key=f"recommendation-job:{job.id}",
        )
        freshness = orchestration_context["snapshots"].latest(
            item.scored_work_id,
            score_type="freshness",
            context_key=f"recommendation-job:{job.id}",
        )
        relevance = orchestration_context["snapshots"].latest(
            item.scored_work_id,
            score_type="relevance",
            context_key=f"recommendation-job:{job.id}",
        )
        assert composite is not None
        assert freshness is not None
        assert relevance is not None
        assert composite.weights == {
            "freshness": 0.0,
            "preference": 0.1,
            "quality": 0.45,
            "relevance": 0.45,
        }
        assert freshness.coverage == 0
        assert freshness.components[0]["reason"] == "fixed_historical_months"
        assert relevance.coverage == 60
        assert relevance.confidence == 60


@pytest.mark.asyncio
async def test_failed_rescore_keeps_formal_set_and_resumes_from_prepared_checkpoint(
    orchestration_context,
) -> None:  # type: ignore[no-untyped-def]
    first_service = _service(orchestration_context)
    first_job = first_service.create_job(
        orchestration_context["run_id"],
        trigger="discovery",
        trigger_job_id="discovery-job",
    )
    await first_service.execute(first_job.id)
    first_set = orchestration_context["results"].latest_for_run(
        orchestration_context["run_id"]
    )
    assert first_set is not None

    flaky = FailOnceSnapshotRepository(
        orchestration_context["snapshots"],
        orchestration_context["works"][1].id,
    )
    flaky.armed = True
    second_service = _service(orchestration_context, snapshots=flaky)
    second_job = second_service.create_job(
        orchestration_context["run_id"],
        trigger="metadata",
        trigger_job_id="metadata-job-1",
    )
    failed = await second_service.execute(second_job.id)

    assert failed.status == "failed"
    assert failed.error_code == "recommendation_scoring_failed"
    assert failed.checkpoint is not None
    assert len(failed.checkpoint["prepared"]) == 2
    assert orchestration_context["results"].latest_for_run(
        orchestration_context["run_id"]
    ).id == first_set.id

    orchestration_context["relevance"].fail_if_called = True
    orchestration_context["jobs"].resume(second_job.id)
    resumed = await second_service.execute(second_job.id)

    assert resumed.status == "succeeded"
    assert resumed.result is not None
    assert orchestration_context["relevance"].calls == 2
    assert len(resumed.checkpoint["completed"]) == 2


def test_auto_trigger_is_idempotent_and_freezes_quality_rule_and_observations(
    orchestration_context,
) -> None:  # type: ignore[no-untyped-def]
    service = _service(orchestration_context)

    first = service.create_job(
        orchestration_context["run_id"],
        trigger="metadata",
        trigger_job_id="metadata-job-2",
    )
    duplicate = service.create_job(
        orchestration_context["run_id"],
        trigger="metadata",
        trigger_job_id="metadata-job-2",
    )

    assert duplicate.id == first.id
    assert first.kind == "recommendation.run"
    assert first.payload["quality_rule"]["rule_sha256"]
    assert set(first.payload["observation_ids_by_work"]) == {
        work.id for work in orchestration_context["works"]
    }
    assert first.payload["fixed_historical_months"] is True
