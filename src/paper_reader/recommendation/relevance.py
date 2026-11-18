"""Deterministic relevance composition and confirmed-keyword evidence."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

RELEVANCE_RULE_VERSION = "relevance-v1"


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class MetricEvidence:
    status: EvidenceStatus
    value: float | None = None
    confidence: float = 100.0
    reason: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        if self.status is EvidenceStatus.OBSERVED:
            if self.value is None or not math.isfinite(self.value) or not 0 <= self.value <= 100:
                raise ValueError("observed value must be between 0 and 100")
            if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 100:
                raise ValueError("confidence must be between 0 and 100")
        elif self.value is not None:
            raise ValueError("unknown and not-applicable evidence cannot contain a value")

    @classmethod
    def observed(
        cls,
        value: float,
        *,
        confidence: float = 100,
        evidence: Mapping[str, Any] | None = None,
    ) -> Self:
        return cls(
            status=EvidenceStatus.OBSERVED,
            value=float(value),
            confidence=float(confidence),
            evidence=evidence or {},
        )

    @classmethod
    def unknown(
        cls,
        *,
        reason: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> Self:
        return cls(
            status=EvidenceStatus.UNKNOWN,
            confidence=0,
            reason=reason,
            evidence=evidence or {},
        )

    @classmethod
    def not_applicable(
        cls,
        *,
        reason: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> Self:
        return cls(
            status=EvidenceStatus.NOT_APPLICABLE,
            confidence=100,
            reason=reason,
            evidence=evidence or {},
        )


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    dimension: str
    configured_weight: float
    effective_weight: float
    status: EvidenceStatus
    value: float | None
    confidence: float
    reason: str | None
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class WeightedScore:
    rule_version: str
    score: float
    coverage: float
    confidence: float
    components: tuple[ScoreComponent, ...]
    degradation_reasons: tuple[str, ...]


def aggregate_weighted_score(
    dimensions: SequenceMetric,
    *,
    rule_version: str,
) -> WeightedScore:
    if not dimensions:
        raise ValueError("at least one score dimension is required")
    if any(weight <= 0 or not math.isfinite(weight) for _, weight, _ in dimensions):
        raise ValueError("configured weights must be finite and positive")

    applicable_weight = sum(
        weight
        for _, weight, metric in dimensions
        if metric.status is not EvidenceStatus.NOT_APPLICABLE
    )
    observed_weight = sum(
        weight
        for _, weight, metric in dimensions
        if metric.status is EvidenceStatus.OBSERVED
    )
    coverage = (
        100.0 * observed_weight / applicable_weight
        if applicable_weight > 0 and observed_weight > 0
        else 0.0
    )

    components: list[ScoreComponent] = []
    score = 0.0
    evidence_confidence = 0.0
    reasons: list[str] = []
    for dimension, configured_weight, metric in dimensions:
        effective_weight = (
            configured_weight / observed_weight
            if metric.status is EvidenceStatus.OBSERVED and observed_weight > 0
            else 0.0
        )
        if metric.status is EvidenceStatus.OBSERVED:
            assert metric.value is not None  # validated by MetricEvidence
            score += metric.value * effective_weight
            evidence_confidence += metric.confidence * effective_weight
        elif metric.status is EvidenceStatus.UNKNOWN:
            reasons.append(metric.reason or f"{dimension}_unknown")
        components.append(
            ScoreComponent(
                dimension=dimension,
                configured_weight=configured_weight,
                effective_weight=round(effective_weight, 6),
                status=metric.status,
                value=metric.value,
                confidence=metric.confidence,
                reason=metric.reason,
                evidence=metric.evidence,
            )
        )

    confidence = (coverage / 100.0) * evidence_confidence if observed_weight > 0 else 0.0
    return WeightedScore(
        rule_version=rule_version,
        score=round(score, 6),
        coverage=round(coverage, 6),
        confidence=round(confidence, 6),
        components=tuple(components),
        degradation_reasons=tuple(reasons),
    )


type SequenceMetric = tuple[tuple[str, float, MetricEvidence], ...]


def score_relevance(
    *,
    lexical: MetricEvidence,
    semantic: MetricEvidence,
    keyword: MetricEvidence,
) -> WeightedScore:
    return aggregate_weighted_score(
        (
            ("lexical", 0.4, lexical),
            ("semantic", 0.4, semantic),
            ("keyword", 0.2, keyword),
        ),
        rule_version=RELEVANCE_RULE_VERSION,
    )


@dataclass(frozen=True, slots=True)
class KeywordAlignment:
    score: float
    eligible: bool
    required_matches: Mapping[str, str]
    boost_matches: Mapping[str, str]
    excluded_matches: Mapping[str, str]


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _group_matches(groups: tuple[ConceptGroup, ...], haystack: str) -> dict[str, str]:
    matches: dict[str, str] = {}
    for group in groups:
        for term in group.terms:
            if _normalized_text(term) in haystack:
                matches[group.key] = term
                break
    return matches


def score_keyword_alignment(
    definition: KeywordSetDefinition,
    *,
    title: str,
    abstract: str | None,
) -> KeywordAlignment:
    haystack = _normalized_text(f"{title}\n{abstract or ''}")
    required = _group_matches(definition.required, haystack)
    boost = _group_matches(definition.boost, haystack)
    excluded = _group_matches(definition.excluded, haystack)
    required_ratio = len(required) / len(definition.required)
    if definition.boost:
        score = (70.0 * required_ratio) + (30.0 * len(boost) / len(definition.boost))
    else:
        score = 100.0 * required_ratio
    eligible = len(required) == len(definition.required) and not excluded
    if excluded:
        score = 0.0
    return KeywordAlignment(
        score=round(score, 6),
        eligible=eligible,
        required_matches=MappingProxyType(required),
        boost_matches=MappingProxyType(boost),
        excluded_matches=MappingProxyType(excluded),
    )
