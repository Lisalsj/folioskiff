"""Objective paper-quality composition with explicit missing-evidence semantics."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from paper_reader.recommendation.relevance import EvidenceStatus, MetricEvidence

QUALITY_RULE_VERSION = "quality-v1"
QUALITY_NEUTRAL_PRIOR = 50.0


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    """The seven independently evidenced dimensions in the v1 quality rubric."""

    citation_performance: MetricEvidence
    venue_rating: MetricEvidence
    author_impact: MetricEvidence
    institution_impact: MetricEvidence
    code_data_impact: MetricEvidence
    research_transparency: MetricEvidence
    publication_maturity: MetricEvidence


@dataclass(frozen=True, slots=True)
class QualityComponent:
    dimension: str
    configured_weight: float
    effective_weight: float
    status: EvidenceStatus
    observed_value: float | None
    imputed_value: float | None
    confidence: float
    reason: str | None
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", dict(self.evidence))


@dataclass(frozen=True, slots=True)
class QualityScore:
    rule_version: str
    score: float
    coverage: float
    confidence: float
    weights: Mapping[str, float]
    components: tuple[QualityComponent, ...]
    degradation_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))


_QUALITY_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("citation_performance", 0.25),
    ("venue_rating", 0.15),
    ("author_impact", 0.10),
    ("institution_impact", 0.10),
    ("code_data_impact", 0.20),
    ("research_transparency", 0.10),
    ("publication_maturity", 0.10),
)


def score_quality(
    metrics: QualityMetrics,
    *,
    weights: Mapping[str, float] | None = None,
    rule_version: str = QUALITY_RULE_VERSION,
) -> QualityScore:
    """Score quality without treating absent evidence as negative evidence.

    Unknown dimensions keep their configured influence but receive the neutral
    prior. Dimensions explicitly marked not applicable are removed from the
    denominator. Coverage and confidence are kept separate from the score.
    """

    configured_weights = _validated_weights(weights)
    selected_rule_version = rule_version.strip()
    if not selected_rule_version:
        raise ValueError("rule_version must not be blank")
    dimensions = tuple(
        (name, configured_weights[name], getattr(metrics, name))
        for name, _ in _QUALITY_WEIGHTS
    )
    applicable_weight = sum(
        weight
        for _, weight, metric in dimensions
        if metric.status is not EvidenceStatus.NOT_APPLICABLE
    )
    if applicable_weight <= 0:
        raise ValueError("at least one quality dimension must be applicable")

    observed_weight = sum(
        weight
        for _, weight, metric in dimensions
        if metric.status is EvidenceStatus.OBSERVED
    )
    coverage = 100.0 * observed_weight / applicable_weight
    observed_confidence = (
        sum(
            weight * metric.confidence
            for _, weight, metric in dimensions
            if metric.status is EvidenceStatus.OBSERVED
        )
        / observed_weight
        if observed_weight > 0
        else 0.0
    )

    score = 0.0
    components: list[QualityComponent] = []
    degradation_reasons: list[str] = []
    for dimension, configured_weight, metric in dimensions:
        if metric.status is EvidenceStatus.NOT_APPLICABLE:
            effective_weight = 0.0
            observed_value = None
            imputed_value = None
        else:
            effective_weight = configured_weight / applicable_weight
            observed_value = metric.value
            imputed_value = (
                QUALITY_NEUTRAL_PRIOR if metric.status is EvidenceStatus.UNKNOWN else None
            )
            contribution = observed_value if observed_value is not None else imputed_value
            assert contribution is not None
            score += contribution * effective_weight
            if metric.status is EvidenceStatus.UNKNOWN:
                degradation_reasons.append(metric.reason or f"{dimension}_unknown")
        components.append(
            QualityComponent(
                dimension=dimension,
                configured_weight=configured_weight,
                effective_weight=round(effective_weight, 6),
                status=metric.status,
                observed_value=observed_value,
                imputed_value=imputed_value,
                confidence=metric.confidence,
                reason=metric.reason,
                evidence=dict(metric.evidence),
            )
        )

    confidence = (coverage / 100.0) * observed_confidence
    return QualityScore(
        rule_version=selected_rule_version,
        score=round(score, 6),
        coverage=round(coverage, 6),
        confidence=round(confidence, 6),
        weights=configured_weights,
        components=tuple(components),
        degradation_reasons=tuple(degradation_reasons),
    )


def _validated_weights(weights: Mapping[str, float] | None) -> Mapping[str, float]:
    expected = tuple(name for name, _ in _QUALITY_WEIGHTS)
    configured = dict(_QUALITY_WEIGHTS) if weights is None else dict(weights)
    if set(configured) != set(expected):
        raise ValueError("quality weights must contain exactly the seven dimensions")
    if any(
        not math.isfinite(value) or value < 0 or value > 1
        for value in configured.values()
    ):
        raise ValueError("quality weights must be between 0 and 1")
    if not math.isclose(sum(configured.values()), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("quality weights must sum to 1")
    return MappingProxyType({name: float(configured[name]) for name in expected})


QUALITY_WEIGHTS = MappingProxyType(dict(_QUALITY_WEIGHTS))
