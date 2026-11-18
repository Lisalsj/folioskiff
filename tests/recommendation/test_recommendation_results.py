from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DatabaseError


@pytest.fixture
def result_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
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
    from paper_reader.discovery.runs import DiscoveryRunRepository, RunBatchSpec
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.results import RecommendationSetRepository
    from paper_reader.recommendation.score_snapshots import ScoreSnapshotRepository

    database = tmp_path / "recommendation-results.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    keywords = KeywordRepository(sessions)
    keyword_version = keywords.create_version(
        research_intent="研究可靠的论文阅读智能体",
        definition=KeywordSetDefinition(
            required=(
                ConceptGroup(
                    key="agent",
                    label="论文智能体",
                    terms=("paper agent", "论文智能体"),
                ),
            ),
        ),
        generator_info={"backend": "fixture", "intent_used_for_filtering": False},
    )
    keywords.confirm(keyword_version.id)
    plan = DiscoveryPlanRepository(sessions).create(
        DiscoveryPlanCreate(
            name="论文智能体月报",
            research_intent="研究可靠的论文阅读智能体",
            keyword_version_id=keyword_version.id,
            date_range=DateRangeSpec(
                mode="fixed",
                start_month="2026-07",
                end_month="2026-07",
            ),
            sources=("arxiv",),
            quota=PlanQuota(priority_limit=2, candidate_limit=5),
        ),
        now=datetime(2026, 7, 15, tzinfo=UTC),
    )
    run = DiscoveryRunRepository(sessions).create_with_job(
        plan_id=plan.id,
        keyword_version_id=keyword_version.id,
        months=("2026-07",),
        plan_snapshot={"priority_limit": 2, "candidate_limit": 5},
        keyword_snapshot={"version_id": keyword_version.id},
        connector_versions={"arxiv": "fixture-v1"},
        batches=(
            RunBatchSpec(
                month="2026-07",
                connector_id="arxiv",
                query_fingerprint="a" * 64,
            ),
        ),
        job_payload={"plan_id": plan.id},
        idempotency_key="recommendation-results-run",
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    identities = IdentityRepository(sessions)
    works = tuple(
        identities.ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id=f"2607.0000{index}v1",
                title=title,
                authors=(author,),
            )
        ).work
        for index, (title, author) in enumerate(
            (
                ("Reliable Paper Agents", "Alice"),
                ("Diverse Paper Agents", "Bob"),
                ("Retracted Paper Agents", "Carol"),
                ("Historical Paper Agents", "Dora"),
            ),
            start=1,
        )
    )
    try:
        yield {
            "sessions": sessions,
            "identities": identities,
            "run": run,
            "works": works,
            "snapshots": ScoreSnapshotRepository(sessions),
            "results": RecommendationSetRepository(sessions),
        }
    finally:
        engine.dispose()


def _candidate(work_id: str, **overrides):  # type: ignore[no-untyped-def]
    from tests.recommendation.test_ranking import candidate

    return candidate(work_id, **overrides)


def _snapshot_refs(repository, run_id: str, paper):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.ranking import score_composite
    from paper_reader.recommendation.results import CandidateSnapshotRefs

    context_key = f"run:{run_id}"

    def record_signal(score_type, signal, rule_version):  # type: ignore[no-untyped-def]
        return repository.record(
            work_id=paper.work_id,
            run_id=run_id,
            context_key=context_key,
            score_type=score_type,
            rule_version=rule_version,
            score=signal.score,
            coverage=signal.coverage,
            confidence=signal.confidence,
            components=(
                {
                    "dimension": score_type,
                    "score": signal.score,
                    "coverage": signal.coverage,
                    "confidence": signal.confidence,
                    "evidence": dict(signal.evidence),
                },
            ),
            weights={score_type: 1.0},
            missing_policy={},
            evidence_observation_ids=(),
            hard_constraints={},
        )

    relevance = record_signal("relevance", paper.relevance, "relevance-v1")
    quality = record_signal("quality", paper.quality, "quality-v1")
    freshness = record_signal("freshness", paper.freshness, "freshness-v1")
    preference = record_signal("preference", paper.preference, "preference-v1")
    composite_score = score_composite(paper, fixed_historical_months=False)
    composite = repository.record(
        work_id=paper.work_id,
        run_id=run_id,
        context_key=context_key,
        score_type="composite",
        rule_version=composite_score.rule_version,
        score=composite_score.score,
        coverage=composite_score.coverage,
        confidence=composite_score.confidence,
        components=tuple(
            {
                "dimension": component.dimension,
                "configured_weight": component.configured_weight,
                "effective_weight": component.effective_weight,
                "included": component.included,
                "score": component.score,
                "coverage": component.coverage,
                "confidence": component.confidence,
                "reason": component.reason,
                "evidence": dict(component.evidence),
            }
            for component in composite_score.components
        ),
        weights={
            component.dimension: component.effective_weight
            for component in composite_score.components
        },
        missing_policy={},
        evidence_observation_ids=(),
        hard_constraints=dict(composite_score.hard_constraints),
    )
    return CandidateSnapshotRefs(
        relevance_snapshot_id=relevance.id,
        quality_snapshot_id=quality.id,
        freshness_snapshot_id=freshness.id,
        preference_snapshot_id=preference.id,
        composite_snapshot_id=composite.id,
    )


def _paper_inputs(context):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.ranking import ExplorationReason

    high, diverse, retracted, historical = context["works"]
    candidates = (
        _candidate(
            high.id,
            relevance=95,
            quality=80,
            freshness=60,
            preference=50,
        ),
        _candidate(
            diverse.id,
            relevance=50,
            quality=100,
            freshness=50,
            preference=50,
            exploration_reasons=(ExplorationReason.NEW_AUTHOR,),
        ),
        _candidate(
            retracted.id,
            relevance=100,
            quality=100,
            freshness=100,
            preference=100,
            retracted=True,
        ),
        _candidate(
            historical.id,
            relevance=90,
            quality=90,
            freshness=90,
            preference=90,
            historical_match=True,
        ),
    )
    snapshots = {
        paper.work_id: _snapshot_refs(
            context["snapshots"],
            context["run"].id,
            paper,
        )
        for paper in candidates
    }
    return candidates, snapshots


def test_recommendation_set_freezes_rank_evidence_snapshots_and_five_views(
    result_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.ranking import RankingView

    candidates, snapshots = _paper_inputs(result_context)
    result = result_context["results"].create(
        run_id=result_context["run"].id,
        candidates=candidates,
        snapshot_refs=snapshots,
        fixed_historical_months=False,
        limit=2,
        candidate_cap=5,
    )

    assert result.version == 1
    assert result.keyword_version_id == result_context["run"].keyword_version_id
    assert result.candidate_count == 3
    assert result.recommendation_count == 2
    assert result.exploration_target == 1
    assert result.exploration_count == 1
    assert len(result.set_sha256) == 64
    assert len(result.items) == 4
    for item in result.items:
        assert (
            len(
                {
                    item.snapshots.relevance_snapshot_id,
                    item.snapshots.quality_snapshot_id,
                    item.snapshots.freshness_snapshot_id,
                    item.snapshots.preference_snapshot_id,
                    item.snapshots.composite_snapshot_id,
                }
            )
            == 5
        )
        assert set(item.ranking_evidence["signals"]) == {
            "relevance",
            "quality",
            "freshness",
            "preference",
            "composite",
        }

    composite = result_context["results"].view(
        result.id,
        view=RankingView.COMPOSITE,
    )
    quality = result_context["results"].view(
        result.id,
        view=RankingView.QUALITY,
    )
    visible = result_context["results"].view(
        result.id,
        view=RankingView.COMPOSITE,
        include_ineligible=True,
    )

    high, diverse, retracted, historical = result_context["works"]
    assert [item.scored_work_id for item in composite.items] == [high.id, diverse.id]
    assert [item.scored_work_id for item in quality.items] == [diverse.id, high.id]
    assert [item.scored_work_id for item in composite.historical_matches] == [historical.id]
    assert historical.id not in {item.scored_work_id for item in composite.items}
    assert retracted.id not in {item.scored_work_id for item in composite.items}
    assert retracted.id in {item.scored_work_id for item in visible.items}


def test_identical_set_is_idempotent_but_changed_snapshot_creates_new_version(
    result_context,
) -> None:  # type: ignore[no-untyped-def]
    candidates, snapshots = _paper_inputs(result_context)
    arguments = {
        "run_id": result_context["run"].id,
        "candidates": candidates,
        "snapshot_refs": snapshots,
        "fixed_historical_months": False,
        "limit": 2,
        "candidate_cap": 5,
    }
    first = result_context["results"].create(**arguments)
    duplicate = result_context["results"].create(**arguments)

    changed_candidate = candidates[0]
    old_snapshot = result_context["snapshots"].latest(
        changed_candidate.work_id,
        score_type="relevance",
        context_key=f"run:{result_context['run'].id}",
    )
    assert old_snapshot is not None
    changed_snapshot = result_context["snapshots"].record(
        work_id=changed_candidate.work_id,
        run_id=result_context["run"].id,
        context_key=old_snapshot.context_key,
        score_type="relevance",
        rule_version=old_snapshot.rule_version,
        score=old_snapshot.score,
        coverage=old_snapshot.coverage,
        confidence=old_snapshot.confidence,
        components=old_snapshot.components,
        weights=old_snapshot.weights,
        missing_policy=old_snapshot.missing_policy,
        evidence_observation_ids=("new-relevance-evidence",),
        hard_constraints=old_snapshot.hard_constraints,
    )
    changed_refs = dict(snapshots)
    changed_refs[changed_candidate.work_id] = replace(
        changed_refs[changed_candidate.work_id],
        relevance_snapshot_id=changed_snapshot.id,
    )
    changed = result_context["results"].create(
        **{
            **arguments,
            "snapshot_refs": changed_refs,
        }
    )

    assert duplicate.id == first.id
    assert changed.id != first.id
    assert changed.version == 2
    assert changed.set_sha256 != first.set_sha256
    assert result_context["results"].get(first.id).set_sha256 == first.set_sha256


@pytest.mark.parametrize("failure_timing", ("before_commit", "after_commit"))
def test_recommendation_snapshot_commit_fault_is_atomic_and_retry_idempotent(
    result_context,
    monkeypatch: pytest.MonkeyPatch,
    failure_timing: str,
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import func, select

    from paper_reader.discovery.models import RecommendationItemRecord, RecommendationSetRecord

    candidates, snapshots = _paper_inputs(result_context)
    arguments = {
        "run_id": result_context["run"].id,
        "candidates": candidates,
        "snapshot_refs": snapshots,
        "fixed_historical_months": False,
        "limit": 2,
        "candidate_cap": 5,
    }
    session_class = result_context["sessions"].class_
    original_commit = session_class.commit
    fault_injected = False

    def commit_with_fault(session) -> None:  # type: ignore[no-untyped-def]
        nonlocal fault_injected
        if fault_injected:
            original_commit(session)
            return
        fault_injected = True
        if failure_timing == "after_commit":
            original_commit(session)
        raise OSError(f"injected recommendation {failure_timing} failure")

    monkeypatch.setattr(session_class, "commit", commit_with_fault)
    with pytest.raises(OSError, match=f"injected recommendation {failure_timing} failure"):
        result_context["results"].create(**arguments)
    monkeypatch.setattr(session_class, "commit", original_commit)

    with result_context["sessions"]() as session:
        sets_after_fault = session.scalar(select(func.count()).select_from(RecommendationSetRecord))
        items_after_fault = session.scalar(
            select(func.count()).select_from(RecommendationItemRecord)
        )
    expected_after_fault = (0, 0) if failure_timing == "before_commit" else (1, 4)
    assert (sets_after_fault, items_after_fault) == expected_after_fault

    recovered = result_context["results"].create(**arguments)
    repeated = result_context["results"].create(**arguments)

    assert recovered.version == 1
    assert repeated.id == recovered.id
    with result_context["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(RecommendationSetRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RecommendationItemRecord)) == 4
        assert session.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchone() is None


def test_recommendation_rows_are_immutable_and_merge_keeps_scored_identity(
    result_context,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.models import RecommendationItemRecord, RecommendationSetRecord
    from tests.discovery.test_merge_reviews import create_ambiguous_pair

    canonical, alias = create_ambiguous_pair(result_context["identities"])
    paper = _candidate(alias.work.id, relevance=80, quality=70)
    refs = _snapshot_refs(
        result_context["snapshots"],
        result_context["run"].id,
        paper,
    )
    result = result_context["results"].create(
        run_id=result_context["run"].id,
        candidates=(paper,),
        snapshot_refs={paper.work_id: refs},
        fixed_historical_months=False,
        limit=1,
        candidate_cap=1,
    )
    original_hash = result.set_sha256

    result_context["identities"].decide_merge_review(
        alias.merge_review.id,
        decision="merge",
    )
    preserved = result_context["results"].get(result.id)

    assert preserved.set_sha256 == original_hash
    assert preserved.items[0].scored_work_id == alias.work.id
    assert canonical.work.id != preserved.items[0].scored_work_id

    sessions = result_context["sessions"]
    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            update(RecommendationItemRecord)
            .where(RecommendationItemRecord.recommendation_set_id == result.id)
            .values(scored_work_id=canonical.work.id)
        )
        session.commit()
    with sessions() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(
            delete(RecommendationSetRecord).where(RecommendationSetRecord.id == result.id)
        )
        session.commit()
