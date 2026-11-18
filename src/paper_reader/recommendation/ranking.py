"""Deterministic composite ranking and explainable exploration quotas."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, Self

from paper_reader.recommendation.integrity import IntegrityAssessment

COMPOSITE_RULE_VERSION = "composite-v1"
DEFAULT_RECOMMENDATION_LIMIT = 20
MAX_CANDIDATE_CAP = 50
EXPLORATION_SHARE = 0.20

_BASE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("relevance", 0.40),
    ("quality", 0.40),
    ("freshness", 0.10),
    ("preference", 0.10),
)


class RankingView(StrEnum):
    COMPOSITE = "composite"
    RELEVANCE = "relevance"
    QUALITY = "quality"
    NEWEST = "newest"
    PREFERENCE = "preference"


class ExplorationReason(StrEnum):
    ADJACENT_TOPIC = "adjacent_topic"
    NEW_AUTHOR = "new_author"
    SOURCE_DIVERSITY = "source_diversity"


class RecommendationChannel(StrEnum):
    EXPLOITATION = "exploitation"
    EXPLORATION = "exploration"


class ScoreResult(Protocol):
    rule_version: str
    score: float
    coverage: float
    confidence: float


@dataclass(frozen=True, slots=True)
class RankingSignal:
    score: float
    coverage: float
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("score", self.score),
            ("coverage", self.coverage),
            ("confidence", self.confidence),
        ):
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
            object.__setattr__(self, name, float(value))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    @classmethod
    def from_score_result(cls, result: ScoreResult) -> Self:
        return cls(
            score=result.score,
            coverage=result.coverage,
            confidence=result.confidence,
            evidence={"rule_version": result.rule_version},
        )


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    work_id: str
    relevance: RankingSignal
    quality: RankingSignal
    freshness: RankingSignal
    preference: RankingSignal
    updated_at: datetime | None
    integrity: IntegrityAssessment
    keyword_eligible: bool = True
    preference_suppressed: bool = False
    historical_match: bool = False
    exploration_reasons: tuple[ExplorationReason, ...] = ()

    def __post_init__(self) -> None:
        work_id = self.work_id.strip()
        if not work_id:
            raise ValueError("work_id must not be blank")
        object.__setattr__(self, "work_id", work_id)
        if self.updated_at is not None:
            if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
                raise ValueError("updated_at must be timezone-aware")
            object.__setattr__(self, "updated_at", self.updated_at.astimezone(UTC))
        reasons: list[ExplorationReason] = []
        seen: set[ExplorationReason] = set()
        for raw_reason in self.exploration_reasons:
            reason = ExplorationReason(raw_reason)
            if reason not in seen:
                reasons.append(reason)
                seen.add(reason)
        object.__setattr__(self, "exploration_reasons", tuple(reasons))

    @property
    def eligible_by_default(self) -> bool:
        return (
            self.keyword_eligible
            and self.integrity.eligible_by_default
            and not self.preference_suppressed
        )

    @property
    def ineligibility_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.keyword_eligible:
            reasons.append("keyword_rules")
        if not self.integrity.eligible_by_default:
            reasons.append(f"integrity:{self.integrity.status.value}")
        if self.preference_suppressed:
            reasons.append("preference:disliked")
        return tuple(reasons)

    def with_signal(self, dimension: str, signal: RankingSignal) -> Self:
        if dimension == "relevance":
            return replace(self, relevance=signal)
        if dimension == "quality":
            return replace(self, quality=signal)
        if dimension == "freshness":
            return replace(self, freshness=signal)
        if dimension == "preference":
            return replace(self, preference=signal)
        raise ValueError(f"unknown ranking dimension: {dimension}")


@dataclass(frozen=True, slots=True)
class CompositeComponent:
    dimension: str
    configured_weight: float
    effective_weight: float
    included: bool
    score: float
    coverage: float
    confidence: float
    reason: str | None
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CompositeScore:
    rule_version: str
    score: float
    coverage: float
    confidence: float
    components: tuple[CompositeComponent, ...]
    eligible_by_default: bool
    ineligibility_reasons: tuple[str, ...]
    hard_constraints: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    rank: int
    candidate: RankingCandidate
    composite: CompositeScore

    @property
    def work_id(self) -> str:
        return self.candidate.work_id

    @property
    def eligible_by_default(self) -> bool:
        return self.composite.eligible_by_default

    @property
    def ineligibility_reasons(self) -> tuple[str, ...]:
        return self.composite.ineligibility_reasons


@dataclass(frozen=True, slots=True)
class RecommendationBatchItem:
    position: int
    work_id: str
    channel: RecommendationChannel
    composite: CompositeScore
    exploration_reasons: tuple[ExplorationReason, ...]


@dataclass(frozen=True, slots=True)
class RecommendationBatch:
    rule_version: str
    requested_limit: int
    candidate_cap: int
    considered_count: int
    exploration_target: int
    exploration_count: int
    items: tuple[RecommendationBatchItem, ...]
    historical_matches: tuple[RankedCandidate, ...]
    degradation_reasons: tuple[str, ...]


def score_composite(
    candidate: RankingCandidate,
    *,
    fixed_historical_months: bool,
) -> CompositeScore:
    weights = _effective_weights(fixed_historical_months)
    score = 0.0
    coverage = 0.0
    confidence = 0.0
    components: list[CompositeComponent] = []
    for dimension, configured_weight in _BASE_WEIGHTS:
        signal = getattr(candidate, dimension)
        effective_weight = weights[dimension]
        included = effective_weight > 0
        reason = (
            "fixed_historical_months"
            if dimension == "freshness" and not included
            else None
        )
        if included:
            score += signal.score * effective_weight
            coverage += signal.coverage * effective_weight
            confidence += signal.confidence * effective_weight
        components.append(
            CompositeComponent(
                dimension=dimension,
                configured_weight=configured_weight,
                effective_weight=effective_weight,
                included=included,
                score=signal.score,
                coverage=signal.coverage,
                confidence=signal.confidence,
                reason=reason,
                evidence=signal.evidence,
            )
        )
    constrained_confidence = candidate.integrity.constrain_confidence(confidence)
    return CompositeScore(
        rule_version=COMPOSITE_RULE_VERSION,
        score=round(score, 6),
        coverage=round(coverage, 6),
        confidence=round(constrained_confidence, 6),
        components=tuple(components),
        eligible_by_default=candidate.eligible_by_default,
        ineligibility_reasons=candidate.ineligibility_reasons,
        hard_constraints=MappingProxyType(
            {
                "integrity": candidate.integrity.to_hard_constraints(),
                "preference": {
                    "suppressed_by_default": candidate.preference_suppressed,
                    "historical_match": candidate.historical_match,
                },
            }
        ),
    )


def rank_candidates(
    candidates: Sequence[RankingCandidate],
    *,
    view: RankingView,
    fixed_historical_months: bool,
    include_ineligible: bool = False,
) -> tuple[RankedCandidate, ...]:
    selected_view = RankingView(view)
    scored = [
        (candidate, score_composite(candidate, fixed_historical_months=fixed_historical_months))
        for candidate in candidates
        if include_ineligible or candidate.eligible_by_default
    ]
    scored.sort(key=lambda item: _sort_key(item[0], item[1], selected_view))
    return tuple(
        RankedCandidate(rank=index, candidate=candidate, composite=composite)
        for index, (candidate, composite) in enumerate(scored, start=1)
    )


def build_recommendation_batch(
    candidates: Sequence[RankingCandidate],
    *,
    fixed_historical_months: bool,
    limit: int = DEFAULT_RECOMMENDATION_LIMIT,
    candidate_cap: int = MAX_CANDIDATE_CAP,
) -> RecommendationBatch:
    if limit < 1:
        raise ValueError("limit must be positive")
    if not limit <= candidate_cap <= MAX_CANDIDATE_CAP:
        raise ValueError("candidate_cap must be between limit and 50")
    ranked = rank_candidates(
        candidates,
        view=RankingView.COMPOSITE,
        fixed_historical_months=fixed_historical_months,
    )
    historical_matches = tuple(
        item for item in ranked if item.candidate.historical_match
    )
    new_candidates = tuple(
        item for item in ranked if not item.candidate.historical_match
    )
    degradation_reasons: list[str] = []
    if len(new_candidates) > candidate_cap:
        degradation_reasons.append("candidate_pool_capped")
    pool = new_candidates[:candidate_cap]
    selected_count = min(limit, len(pool))
    if selected_count < limit:
        degradation_reasons.append("insufficient_eligible_candidates")
    exploration_target = (
        math.ceil(selected_count * EXPLORATION_SHARE) if selected_count else 0
    )
    exploitation_target = selected_count - exploration_target
    exploitation = list(pool[:exploitation_target])
    remaining = list(pool[exploitation_target:])
    exploration_candidates = [
        item for item in remaining if item.candidate.exploration_reasons
    ]
    exploration = _select_exploration(
        exploration_candidates,
        target=exploration_target,
    )
    exploration_ids = {item.work_id for item in exploration}
    filler_count = exploration_target - len(exploration)
    filler = [item for item in remaining if item.work_id not in exploration_ids][
        :filler_count
    ]
    if len(exploration) < exploration_target:
        degradation_reasons.append(
            "exploration_candidates_unavailable"
            if not exploration
            else "exploration_quota_partial"
        )

    batch_items: list[RecommendationBatchItem] = []
    for ranked_item in (*exploitation, *filler):
        batch_items.append(
            RecommendationBatchItem(
                position=len(batch_items) + 1,
                work_id=ranked_item.work_id,
                channel=RecommendationChannel.EXPLOITATION,
                composite=ranked_item.composite,
                exploration_reasons=(),
            )
        )
    for ranked_item in exploration:
        batch_items.append(
            RecommendationBatchItem(
                position=len(batch_items) + 1,
                work_id=ranked_item.work_id,
                channel=RecommendationChannel.EXPLORATION,
                composite=ranked_item.composite,
                exploration_reasons=ranked_item.candidate.exploration_reasons,
            )
        )
    return RecommendationBatch(
        rule_version=COMPOSITE_RULE_VERSION,
        requested_limit=limit,
        candidate_cap=candidate_cap,
        considered_count=len(pool),
        exploration_target=exploration_target,
        exploration_count=len(exploration),
        items=tuple(batch_items),
        historical_matches=historical_matches,
        degradation_reasons=tuple(degradation_reasons),
    )


def _effective_weights(fixed_historical_months: bool) -> Mapping[str, float]:
    if fixed_historical_months:
        return MappingProxyType(
            {"relevance": 0.45, "quality": 0.45, "freshness": 0.0, "preference": 0.10}
        )
    return MappingProxyType(dict(_BASE_WEIGHTS))


def _select_exploration(
    candidates: Sequence[RankedCandidate],
    *,
    target: int,
) -> list[RankedCandidate]:
    remaining = list(candidates)
    selected: list[RankedCandidate] = []
    covered_reasons: set[ExplorationReason] = set()
    while remaining and len(selected) < target:
        remaining.sort(
            key=lambda item: (
                -len(set(item.candidate.exploration_reasons) - covered_reasons),
                -len(item.candidate.exploration_reasons),
                -item.composite.score,
                item.work_id,
            )
        )
        chosen = remaining.pop(0)
        selected.append(chosen)
        covered_reasons.update(chosen.candidate.exploration_reasons)
    return selected


def _sort_key(
    candidate: RankingCandidate,
    composite: CompositeScore,
    view: RankingView,
) -> tuple[float, float, float, float, str]:
    return ranking_view_sort_key(
        view=view,
        composite_score=composite.score,
        relevance_score=candidate.relevance.score,
        quality_score=candidate.quality.score,
        preference_score=candidate.preference.score,
        updated_at=candidate.updated_at,
        work_id=candidate.work_id,
    )


def ranking_view_sort_key(
    *,
    view: RankingView,
    composite_score: float,
    relevance_score: float,
    quality_score: float,
    preference_score: float,
    updated_at: datetime | None,
    work_id: str,
) -> tuple[float, float, float, float, str]:
    """Return the shared deterministic key for already-frozen ranking signals."""

    selected_view = RankingView(view)
    timestamp = updated_at.timestamp() if updated_at is not None else -math.inf
    if selected_view is RankingView.RELEVANCE:
        return (
            -relevance_score,
            -composite_score,
            -quality_score,
            -timestamp,
            work_id,
        )
    if selected_view is RankingView.QUALITY:
        return (
            -quality_score,
            -composite_score,
            -relevance_score,
            -timestamp,
            work_id,
        )
    if selected_view is RankingView.NEWEST:
        return (
            -timestamp,
            -composite_score,
            -relevance_score,
            -quality_score,
            work_id,
        )
    if selected_view is RankingView.PREFERENCE:
        return (
            -preference_score,
            -composite_score,
            -relevance_score,
            -quality_score,
            work_id,
        )
    return (
        -composite_score,
        -relevance_score,
        -quality_score,
        -timestamp,
        work_id,
    )
