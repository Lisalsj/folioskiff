from __future__ import annotations

from datetime import UTC, datetime

import pytest


def signal(
    score: float,
    *,
    coverage: float = 100,
    confidence: float = 100,
):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.ranking import RankingSignal

    return RankingSignal(
        score=score,
        coverage=coverage,
        confidence=confidence,
        evidence={"fixture": score},
    )


def integrity(*, retracted: bool = False, concern: bool = False):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.integrity import assess_integrity
    from tests.recommendation.test_integrity_rules import (
        crossref_update,
        observed_result,
    )

    if retracted:
        return assess_integrity(
            (observed_result("openalex", {"is_retracted": True}),)
        )
    if concern:
        return assess_integrity(
            (
                observed_result(
                    "crossref",
                    {
                        "updates": [
                            crossref_update(
                                "expression_of_concern",
                                "2026-06-01T00:00:00Z",
                            )
                        ]
                    },
                ),
            )
        )
    return assess_integrity(())


def candidate(
    work_id: str,
    *,
    relevance: float = 50,
    quality: float = 50,
    freshness: float = 50,
    preference: float = 50,
    updated_at: datetime | None = None,
    retracted: bool = False,
    concern: bool = False,
    keyword_eligible: bool = True,
    preference_suppressed: bool = False,
    historical_match: bool = False,
    exploration_reasons=(),  # type: ignore[no-untyped-def]
):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.ranking import RankingCandidate

    return RankingCandidate(
        work_id=work_id,
        relevance=signal(relevance),
        quality=signal(quality),
        freshness=signal(freshness),
        preference=signal(preference),
        updated_at=updated_at or datetime(2026, 7, 1, tzinfo=UTC),
        integrity=integrity(retracted=retracted, concern=concern),
        keyword_eligible=keyword_eligible,
        preference_suppressed=preference_suppressed,
        historical_match=historical_match,
        exploration_reasons=exploration_reasons,
    )


def test_composite_uses_40_40_10_10_and_preserves_coverage_confidence() -> None:
    from paper_reader.recommendation.ranking import score_composite

    paper = candidate(
        "paper-1",
        relevance=90,
        quality=70,
        freshness=100,
        preference=50,
    )
    paper = paper.with_signal(
        "preference",
        signal(50, coverage=0, confidence=0),
    )

    result = score_composite(paper, fixed_historical_months=False)

    assert result.rule_version == "composite-v1"
    assert result.score == 79
    assert result.coverage == 90
    assert result.confidence == 90
    assert [
        (component.dimension, component.configured_weight, component.effective_weight)
        for component in result.components
    ] == [
        ("relevance", 0.4, 0.4),
        ("quality", 0.4, 0.4),
        ("freshness", 0.1, 0.1),
        ("preference", 0.1, 0.1),
    ]


def test_fixed_historical_months_transfer_freshness_evenly() -> None:
    from paper_reader.recommendation.ranking import score_composite

    paper = candidate(
        "paper-1",
        relevance=90,
        quality=70,
        freshness=0,
        preference=50,
    ).with_signal("freshness", signal(0, coverage=0, confidence=0))

    result = score_composite(paper, fixed_historical_months=True)

    assert result.score == 77
    assert result.coverage == 100
    assert result.confidence == 100
    assert [component.effective_weight for component in result.components] == [
        0.45,
        0.45,
        0,
        0.1,
    ]
    assert result.components[2].included is False
    assert result.components[2].reason == "fixed_historical_months"


def test_integrity_constraints_apply_after_scoring_and_before_default_ranking() -> None:
    from paper_reader.recommendation.ranking import RankingView, rank_candidates

    retracted = candidate(
        "retracted",
        relevance=100,
        quality=100,
        freshness=100,
        preference=100,
        retracted=True,
    )
    concern = candidate(
        "concern",
        relevance=90,
        quality=90,
        freshness=90,
        preference=90,
        concern=True,
    )
    safe = candidate("safe", relevance=50, quality=50, freshness=50, preference=50)

    default = rank_candidates(
        (retracted, concern, safe),
        view=RankingView.COMPOSITE,
        fixed_historical_months=False,
    )
    visible = rank_candidates(
        (retracted, concern, safe),
        view=RankingView.COMPOSITE,
        fixed_historical_months=False,
        include_ineligible=True,
    )

    assert [item.work_id for item in default] == ["concern", "safe"]
    assert default[0].composite.score == 90
    assert default[0].composite.confidence == 40
    assert default[0].eligible_by_default is True
    assert [item.work_id for item in visible] == ["retracted", "concern", "safe"]
    assert visible[0].eligible_by_default is False
    assert visible[0].ineligibility_reasons == ("integrity:retracted",)


def test_keyword_ineligible_candidate_is_filtered_before_scores() -> None:
    from paper_reader.recommendation.ranking import RankingView, rank_candidates

    ranked = rank_candidates(
        (
            candidate("excluded", relevance=100, keyword_eligible=False),
            candidate("eligible", relevance=10),
        ),
        view=RankingView.RELEVANCE,
        fixed_historical_months=False,
    )

    assert [item.work_id for item in ranked] == ["eligible"]


def test_disliked_candidate_is_suppressed_by_default_but_remains_viewable() -> None:
    from paper_reader.recommendation.ranking import RankingView, rank_candidates

    disliked = candidate(
        "disliked",
        relevance=100,
        quality=100,
        preference=0,
        preference_suppressed=True,
    )
    ordinary = candidate("ordinary", relevance=10, quality=10)

    default = rank_candidates(
        (disliked, ordinary),
        view=RankingView.COMPOSITE,
        fixed_historical_months=False,
    )
    visible = rank_candidates(
        (disliked, ordinary),
        view=RankingView.COMPOSITE,
        fixed_historical_months=False,
        include_ineligible=True,
    )

    assert [item.work_id for item in default] == ["ordinary"]
    assert [item.work_id for item in visible] == ["disliked", "ordinary"]
    assert visible[0].ineligibility_reasons == ("preference:disliked",)


def test_five_views_have_deterministic_primary_and_tie_breaks() -> None:
    from paper_reader.recommendation.ranking import RankingView, rank_candidates

    papers = (
        candidate("balanced", relevance=80, quality=80, freshness=80, preference=80),
        candidate("relevance", relevance=100, quality=40, freshness=40, preference=40),
        candidate("quality", relevance=40, quality=100, freshness=40, preference=40),
        candidate(
            "newest",
            relevance=40,
            quality=40,
            freshness=100,
            preference=40,
            updated_at=datetime(2026, 7, 13, tzinfo=UTC),
        ),
        candidate("preference", relevance=40, quality=40, freshness=40, preference=100),
        candidate("balanced-z", relevance=80, quality=80, freshness=80, preference=80),
    )

    first_by_view = {
        view: rank_candidates(
            papers,
            view=view,
            fixed_historical_months=False,
        )[0].work_id
        for view in RankingView
    }

    assert first_by_view == {
        RankingView.COMPOSITE: "balanced",
        RankingView.RELEVANCE: "relevance",
        RankingView.QUALITY: "quality",
        RankingView.NEWEST: "newest",
        RankingView.PREFERENCE: "preference",
    }


def test_shared_view_sort_key_uses_only_frozen_signals() -> None:
    from paper_reader.recommendation.ranking import RankingView, ranking_view_sort_key

    facts = {
        "composite_score": 70,
        "relevance_score": 60,
        "quality_score": 90,
        "preference_score": 40,
        "updated_at": datetime(2026, 7, 1, tzinfo=UTC),
        "work_id": "paper-a",
    }

    assert ranking_view_sort_key(view=RankingView.QUALITY, **facts) == (
        -90,
        -70,
        -60,
        -datetime(2026, 7, 1, tzinfo=UTC).timestamp(),
        "paper-a",
    )


def test_default_batch_reserves_four_of_twenty_for_explainable_exploration() -> None:
    from paper_reader.recommendation.ranking import (
        ExplorationReason,
        RecommendationChannel,
        build_recommendation_batch,
    )

    papers = []
    for index in range(25):
        reasons = ()
        if 16 <= index < 20:
            reasons = (
                ExplorationReason.ADJACENT_TOPIC,
                ExplorationReason.NEW_AUTHOR,
                ExplorationReason.SOURCE_DIVERSITY,
            )[: 20 - index]
        papers.append(
            candidate(
                f"paper-{index:02d}",
                relevance=100 - index,
                quality=100 - index,
                freshness=100 - index,
                preference=100 - index,
                exploration_reasons=reasons,
            )
        )

    batch = build_recommendation_batch(
        tuple(papers),
        fixed_historical_months=False,
    )

    assert len(batch.items) == 20
    assert batch.candidate_cap == 50
    assert batch.exploration_target == 4
    assert batch.exploration_count == 4
    assert sum(
        item.channel is RecommendationChannel.EXPLOITATION for item in batch.items
    ) == 16
    assert [item.work_id for item in batch.items[-4:]] == [
        "paper-16",
        "paper-17",
        "paper-18",
        "paper-19",
    ]
    assert batch.items[-1].exploration_reasons == (
        ExplorationReason.ADJACENT_TOPIC,
    )
    assert batch.degradation_reasons == ()


def test_exploration_shortfall_fills_batch_without_exceeding_candidate_cap() -> None:
    from paper_reader.recommendation.ranking import (
        ExplorationReason,
        RecommendationChannel,
        build_recommendation_batch,
    )

    papers = tuple(
        candidate(
            f"paper-{index:02d}",
            relevance=100 - index,
            quality=100 - index,
            freshness=100 - index,
            preference=100 - index,
            exploration_reasons=(
                (ExplorationReason.NEW_AUTHOR,) if index == 50 else ()
            ),
        )
        for index in range(51)
    )

    batch = build_recommendation_batch(
        papers,
        fixed_historical_months=False,
    )

    assert len(batch.items) == 20
    assert batch.considered_count == 50
    assert batch.exploration_count == 0
    assert all(item.channel is RecommendationChannel.EXPLOITATION for item in batch.items)
    assert "exploration_candidates_unavailable" in batch.degradation_reasons
    assert "paper-50" not in {item.work_id for item in batch.items}


def test_exploration_greedily_covers_distinct_reason_categories() -> None:
    from paper_reader.recommendation.ranking import (
        ExplorationReason,
        build_recommendation_batch,
    )

    papers = [
        candidate(
            f"exploit-{index:02d}",
            relevance=100 - index,
            quality=100 - index,
            freshness=100 - index,
            preference=100 - index,
        )
        for index in range(16)
    ]
    papers.extend(
        (
            candidate(
                "adjacent-high-1",
                relevance=80,
                quality=80,
                freshness=80,
                preference=80,
                exploration_reasons=(ExplorationReason.ADJACENT_TOPIC,),
            ),
            candidate(
                "adjacent-high-2",
                relevance=79,
                quality=79,
                freshness=79,
                preference=79,
                exploration_reasons=(ExplorationReason.ADJACENT_TOPIC,),
            ),
            candidate(
                "adjacent-high-3",
                relevance=78,
                quality=78,
                freshness=78,
                preference=78,
                exploration_reasons=(ExplorationReason.ADJACENT_TOPIC,),
            ),
            candidate(
                "adjacent-high-4",
                relevance=77,
                quality=77,
                freshness=77,
                preference=77,
                exploration_reasons=(ExplorationReason.ADJACENT_TOPIC,),
            ),
            candidate(
                "new-author",
                relevance=60,
                quality=60,
                freshness=60,
                preference=60,
                exploration_reasons=(ExplorationReason.NEW_AUTHOR,),
            ),
            candidate(
                "new-source",
                relevance=50,
                quality=50,
                freshness=50,
                preference=50,
                exploration_reasons=(ExplorationReason.SOURCE_DIVERSITY,),
            ),
        )
    )

    batch = build_recommendation_batch(
        tuple(papers),
        fixed_historical_months=False,
    )
    explored = batch.items[-4:]

    assert {reason for item in explored for reason in item.exploration_reasons} == {
        ExplorationReason.ADJACENT_TOPIC,
        ExplorationReason.NEW_AUTHOR,
        ExplorationReason.SOURCE_DIVERSITY,
    }


def test_historical_matches_are_separate_and_do_not_consume_new_paper_quota() -> None:
    from paper_reader.recommendation.ranking import build_recommendation_batch

    papers = tuple(
        candidate(
            f"new-{index:02d}",
            relevance=90 - index,
            quality=90 - index,
        )
        for index in range(20)
    ) + tuple(
        candidate(
            f"history-{index}",
            relevance=100,
            quality=100,
            historical_match=True,
        )
        for index in range(3)
    )

    batch = build_recommendation_batch(
        papers,
        fixed_historical_months=False,
    )

    assert len(batch.items) == 20
    assert {item.work_id for item in batch.items} == {
        f"new-{index:02d}" for index in range(20)
    }
    assert [item.work_id for item in batch.historical_matches] == [
        "history-0",
        "history-1",
        "history-2",
    ]
    assert batch.considered_count == 20


def test_ranking_signal_adapts_existing_score_results_without_losing_certainty() -> None:
    from paper_reader.recommendation.quality import score_quality
    from paper_reader.recommendation.ranking import RankingSignal
    from paper_reader.recommendation.relevance import MetricEvidence, score_relevance
    from tests.recommendation.test_quality_scoring import observed_quality_metrics

    relevance = score_relevance(
        lexical=MetricEvidence.observed(80),
        semantic=MetricEvidence.unknown(reason="semantic_unavailable"),
        keyword=MetricEvidence.observed(60),
    )
    quality = score_quality(observed_quality_metrics())

    relevance_signal = RankingSignal.from_score_result(relevance)
    quality_signal = RankingSignal.from_score_result(quality)

    assert relevance_signal.score == relevance.score
    assert relevance_signal.coverage == relevance.coverage
    assert relevance_signal.confidence == relevance.confidence
    assert relevance_signal.evidence == {"rule_version": "relevance-v1"}
    assert quality_signal.evidence == {"rule_version": "quality-v1"}


def test_ranking_contract_rejects_invalid_signals_and_naive_dates() -> None:
    from paper_reader.recommendation.ranking import RankingCandidate, RankingSignal

    with pytest.raises(ValueError, match="score"):
        RankingSignal(score=float("nan"), coverage=100, confidence=100)
    with pytest.raises(ValueError, match="coverage"):
        RankingSignal(score=50, coverage=-1, confidence=100)
    with pytest.raises(ValueError, match="confidence"):
        RankingSignal(score=50, coverage=100, confidence=101)
    with pytest.raises(ValueError, match="timezone-aware"):
        RankingCandidate(
            work_id="paper",
            relevance=signal(50),
            quality=signal(50),
            freshness=signal(50),
            preference=signal(50),
            updated_at=datetime(2026, 7, 1),
            integrity=integrity(),
        )
