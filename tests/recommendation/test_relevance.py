from __future__ import annotations

import pytest


def test_relevance_uses_fixed_40_40_20_weights_with_traceable_components() -> None:
    from paper_reader.recommendation.relevance import MetricEvidence, score_relevance

    result = score_relevance(
        lexical=MetricEvidence.observed(80, evidence={"query": "diffusion"}),
        semantic=MetricEvidence.observed(60, evidence={"model": "multilingual-e5-small"}),
        keyword=MetricEvidence.observed(100, evidence={"version_id": "keywords-v3"}),
    )

    assert result.rule_version == "relevance-v1"
    assert result.score == 76.0
    assert result.coverage == 100.0
    assert result.confidence == 100.0
    weights = [
        (item.dimension, item.configured_weight, item.effective_weight)
        for item in result.components
    ]
    assert weights == [
        ("lexical", 0.4, 0.4),
        ("semantic", 0.4, 0.4),
        ("keyword", 0.2, 0.2),
    ]
    assert result.components[1].evidence == {"model": "multilingual-e5-small"}


def test_missing_semantic_evidence_reweights_score_but_lowers_coverage_and_confidence() -> None:
    from paper_reader.recommendation.relevance import MetricEvidence, score_relevance

    result = score_relevance(
        lexical=MetricEvidence.observed(80),
        semantic=MetricEvidence.unknown(reason="semantic_runtime_unavailable"),
        keyword=MetricEvidence.observed(100),
    )

    assert result.score == 86.666667
    assert result.coverage == 60.0
    assert result.confidence == 60.0
    assert result.degradation_reasons == ("semantic_runtime_unavailable",)
    assert result.components[0].effective_weight == 0.666667
    assert result.components[1].effective_weight == 0.0
    assert result.components[2].effective_weight == 0.333333


def test_not_applicable_dimension_is_removed_without_a_coverage_penalty() -> None:
    from paper_reader.recommendation.relevance import MetricEvidence, score_relevance

    result = score_relevance(
        lexical=MetricEvidence.observed(80),
        semantic=MetricEvidence.not_applicable(reason="historical_fixture_has_no_full_text"),
        keyword=MetricEvidence.observed(100),
    )

    assert result.score == 86.666667
    assert result.coverage == 100.0
    assert result.confidence == 100.0
    assert result.degradation_reasons == ()


def test_component_reliability_contributes_to_confidence_without_changing_score() -> None:
    from paper_reader.recommendation.relevance import MetricEvidence, score_relevance

    result = score_relevance(
        lexical=MetricEvidence.observed(80),
        semantic=MetricEvidence.observed(60, confidence=80),
        keyword=MetricEvidence.observed(100),
    )

    assert result.score == 76.0
    assert result.coverage == 100.0
    assert result.confidence == 92.0


def test_no_observed_relevance_returns_zero_with_no_false_certainty() -> None:
    from paper_reader.recommendation.relevance import MetricEvidence, score_relevance

    result = score_relevance(
        lexical=MetricEvidence.unknown(reason="index_empty"),
        semantic=MetricEvidence.unknown(reason="model_missing"),
        keyword=MetricEvidence.unknown(reason="keyword_version_missing"),
    )

    assert result.score == 0.0
    assert result.coverage == 0.0
    assert result.confidence == 0.0
    assert result.degradation_reasons == (
        "index_empty",
        "model_missing",
        "keyword_version_missing",
    )


@pytest.mark.parametrize("value", [-0.01, 100.01, float("nan"), float("inf")])
def test_metric_evidence_rejects_out_of_range_or_non_finite_values(value: float) -> None:
    from paper_reader.recommendation.relevance import MetricEvidence

    with pytest.raises(ValueError, match="between 0 and 100"):
        MetricEvidence.observed(value)


def test_keyword_alignment_scores_required_boost_and_exclusion_groups() -> None:
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition
    from paper_reader.recommendation.relevance import score_keyword_alignment

    definition = KeywordSetDefinition(
        required=(
            ConceptGroup(key="diffusion", label="扩散模型", terms=("diffusion", "扩散模型")),
            ConceptGroup(key="cancer", label="癌症", terms=("cancer", "癌症")),
        ),
        boost=(
            ConceptGroup(key="imaging", label="影像", terms=("MRI", "医学影像")),
            ConceptGroup(key="segmentation", label="分割", terms=("segmentation", "分割")),
        ),
        excluded=(
            ConceptGroup(key="survey", label="综述", terms=("survey", "综述")),
        ),
    )

    aligned = score_keyword_alignment(
        definition,
        title="扩散模型用于癌症医学影像",
        abstract="研究可靠诊断。",
    )
    excluded = score_keyword_alignment(
        definition,
        title="A survey of diffusion models for cancer MRI",
        abstract=None,
    )

    assert aligned.eligible is True
    assert aligned.score == 85.0
    assert aligned.required_matches == {"diffusion": "扩散模型", "cancer": "癌症"}
    assert aligned.boost_matches == {"imaging": "医学影像"}
    assert aligned.excluded_matches == {}
    assert excluded.eligible is False
    assert excluded.score == 0.0
    assert excluded.excluded_matches == {"survey": "survey"}
