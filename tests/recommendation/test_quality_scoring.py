from __future__ import annotations

import pytest


def observed_quality_metrics(*, citation_confidence: float = 100):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.quality import QualityMetrics
    from paper_reader.recommendation.relevance import MetricEvidence

    return QualityMetrics(
        citation_performance=MetricEvidence.observed(80, confidence=citation_confidence),
        venue_rating=MetricEvidence.observed(60),
        author_impact=MetricEvidence.observed(70),
        institution_impact=MetricEvidence.observed(50),
        code_data_impact=MetricEvidence.observed(90),
        research_transparency=MetricEvidence.observed(40),
        publication_maturity=MetricEvidence.observed(100),
    )


def test_quality_uses_fixed_seven_dimension_weights_with_traceable_components() -> None:
    from paper_reader.recommendation.quality import score_quality

    result = score_quality(observed_quality_metrics(citation_confidence=80))

    assert result.rule_version == "quality-v1"
    assert result.score == 73.0
    assert result.coverage == 100.0
    assert result.confidence == 95.0
    assert [
        (component.dimension, component.configured_weight, component.effective_weight)
        for component in result.components
    ] == [
        ("citation_performance", 0.25, 0.25),
        ("venue_rating", 0.15, 0.15),
        ("author_impact", 0.1, 0.1),
        ("institution_impact", 0.1, 0.1),
        ("code_data_impact", 0.2, 0.2),
        ("research_transparency", 0.1, 0.1),
        ("publication_maturity", 0.1, 0.1),
    ]
    assert result.components[0].observed_value == 80
    assert result.components[0].imputed_value is None


def test_unknown_quality_uses_neutral_prior_without_masquerading_as_observed_evidence() -> None:
    from paper_reader.recommendation.quality import QualityMetrics, score_quality
    from paper_reader.recommendation.relevance import MetricEvidence

    result = score_quality(
        QualityMetrics(
            citation_performance=MetricEvidence.observed(80),
            venue_rating=MetricEvidence.observed(60),
            author_impact=MetricEvidence.unknown(reason="author_metrics_missing"),
            institution_impact=MetricEvidence.unknown(reason="institution_rule_missing"),
            code_data_impact=MetricEvidence.unknown(reason="code_link_missing"),
            research_transparency=MetricEvidence.unknown(reason="transparency_unknown"),
            publication_maturity=MetricEvidence.unknown(reason="publication_type_unknown"),
        )
    )

    assert result.score == 59.0
    assert result.coverage == 40.0
    assert result.confidence == 40.0
    assert result.components[2].observed_value is None
    assert result.components[2].imputed_value == 50
    assert result.degradation_reasons == (
        "author_metrics_missing",
        "institution_rule_missing",
        "code_link_missing",
        "transparency_unknown",
        "publication_type_unknown",
    )


def test_not_applicable_quality_dimension_is_removed_without_coverage_penalty() -> None:
    from dataclasses import replace

    from paper_reader.recommendation.quality import score_quality
    from paper_reader.recommendation.relevance import MetricEvidence

    metrics = replace(
        observed_quality_metrics(),
        code_data_impact=MetricEvidence.not_applicable(
            reason="publication_type_has_no_code_artifact"
        ),
    )
    result = score_quality(metrics)

    assert result.score == 68.75
    assert result.coverage == 100.0
    assert result.confidence == 100.0
    assert result.components[4].effective_weight == 0
    assert result.components[4].imputed_value is None
    assert result.degradation_reasons == ()


def test_all_unknown_quality_is_neutral_with_zero_coverage_and_confidence() -> None:
    from paper_reader.recommendation.quality import QualityMetrics, score_quality
    from paper_reader.recommendation.relevance import MetricEvidence

    unknown = MetricEvidence.unknown(reason="not_collected")
    result = score_quality(
        QualityMetrics(
            citation_performance=unknown,
            venue_rating=unknown,
            author_impact=unknown,
            institution_impact=unknown,
            code_data_impact=unknown,
            research_transparency=unknown,
            publication_maturity=unknown,
        )
    )

    assert result.score == 50.0
    assert result.coverage == 0.0
    assert result.confidence == 0.0


def test_quality_rejects_a_configuration_with_no_applicable_dimension() -> None:
    from paper_reader.recommendation.quality import QualityMetrics, score_quality
    from paper_reader.recommendation.relevance import MetricEvidence

    omitted = MetricEvidence.not_applicable(reason="invalid_fixture")
    with pytest.raises(ValueError, match="applicable"):
        score_quality(
            QualityMetrics(
                citation_performance=omitted,
                venue_rating=omitted,
                author_impact=omitted,
                institution_impact=omitted,
                code_data_impact=omitted,
                research_transparency=omitted,
                publication_maturity=omitted,
            )
        )
