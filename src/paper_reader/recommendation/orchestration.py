"""Durable evidence-first orchestration for immutable recommendation sets."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from statistics import median
from typing import Any, Literal, Protocol, cast

from paper_reader.discovery.runs import DiscoveryRun, DiscoveryRunRepository
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.evidence_builder import (
    QualityEvidence,
    QualityEvidenceBuilder,
)
from paper_reader.recommendation.integrity import (
    IntegrityAssessment,
    IntegrityStatus,
    IntegrityWarning,
)
from paper_reader.recommendation.preferences import PreferenceRepository, PreferenceScore
from paper_reader.recommendation.ranking import (
    COMPOSITE_RULE_VERSION,
    ExplorationReason,
    RankingCandidate,
    RankingSignal,
    score_composite,
)
from paper_reader.recommendation.results import (
    CandidateSnapshotRefs,
    RecommendationSet,
    RecommendationSetRepository,
)
from paper_reader.recommendation.score_rules import ScoreRuleRepository
from paper_reader.recommendation.score_snapshots import (
    ScoreSnapshot,
    ScoreSnapshotRepository,
)
from paper_reader.recommendation.service import RunRelevanceResult

RECOMMENDATION_JOB_KIND = "recommendation.run"
RECOMMENDATION_PAYLOAD_VERSION = "recommendation-run-v1"
FRESHNESS_RULE_VERSION = "freshness-v1"

RecommendationTrigger = Literal["discovery", "metadata", "manual"]


class RunRelevanceScorer(Protocol):
    def score_run(self, run_id: str) -> RunRelevanceResult: ...


class DynamicRunRelevanceScorer:
    """Resolve the optional semantic runtime at the moment a job is prepared."""

    def __init__(self, factory: Callable[[], RunRelevanceScorer]) -> None:
        self._factory = factory

    def score_run(self, run_id: str) -> RunRelevanceResult:
        return self._factory().score_run(run_id)


class ScoreSnapshotStore(Protocol):
    def record(
        self,
        *,
        work_id: str,
        context_key: str,
        score_type: Literal[
            "relevance", "quality", "freshness", "preference", "composite"
        ],
        rule_version: str,
        score: float,
        coverage: float,
        confidence: float,
        components: Sequence[Mapping[str, Any]],
        weights: Mapping[str, float],
        missing_policy: Mapping[str, Any],
        evidence_observation_ids: Sequence[str] = (),
        hard_constraints: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> ScoreSnapshot: ...


class RecommendationResultStore(Protocol):
    def create(
        self,
        *,
        run_id: str,
        candidates: Sequence[RankingCandidate],
        snapshot_refs: Mapping[str, CandidateSnapshotRefs],
        fixed_historical_months: bool,
        limit: int,
        candidate_cap: int,
        ranking_rule_version: str = COMPOSITE_RULE_VERSION,
    ) -> RecommendationSet: ...


class RecommendationOrchestrator:
    """Freeze inputs, persist five score facts, then atomically publish one set."""

    def __init__(
        self,
        *,
        runs: DiscoveryRunRepository,
        jobs: JobRepository,
        relevance: RunRelevanceScorer,
        quality: QualityEvidenceBuilder,
        preferences: PreferenceRepository,
        rules: ScoreRuleRepository,
        snapshots: ScoreSnapshotStore,
        results: RecommendationResultStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runs = runs
        self._jobs = jobs
        self._relevance = relevance
        self._quality = quality
        self._preferences = preferences
        self._rules = rules
        self._snapshots = snapshots
        self._results = results
        self._clock = clock

    def create_job(
        self,
        run_id: str,
        *,
        trigger: RecommendationTrigger = "manual",
        trigger_job_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        selected_trigger = self._trigger(trigger)
        run = self._runs.get(run_id)
        if run.status not in {"succeeded", "partial_success"}:
            raise ValueError("discovery run must be terminal before recommendation")

        namespaced_key = self._idempotency_key(
            run_id=run.id,
            trigger=selected_trigger,
            trigger_job_id=trigger_job_id,
            supplied=idempotency_key,
        )
        if namespaced_key is not None:
            existing = self._jobs.get_by_idempotency_key(namespaced_key)
            if existing is not None:
                self._validate_existing_job(existing, run.id, selected_trigger)
                return existing

        work_ids = tuple(dict.fromkeys(self._runs.list_candidate_work_ids(run.id)))
        rule = self._rules.get_active_quality_rule()
        observation_ids = self._quality.freeze_observation_ids(work_ids)
        fixed_historical = self._fixed_historical_months(run)
        quota = self._quota(run)
        scoring_at = self._aware(self._clock())
        payload: dict[str, Any] = {
            "schema_version": RECOMMENDATION_PAYLOAD_VERSION,
            "run_id": run.id,
            "keyword_version_id": run.keyword_version_id,
            "work_ids": list(work_ids),
            "trigger": selected_trigger,
            "trigger_job_id": trigger_job_id,
            "scoring_at": scoring_at.isoformat(),
            "fixed_historical_months": fixed_historical,
            "limit": quota[0],
            "candidate_cap": quota[1],
            "quality_rule": {
                "id": rule.id,
                "rule_version": rule.rule_version,
                "rule_sha256": rule.rule_sha256,
                "weights": dict(rule.weights),
            },
            "observation_ids_by_work": {
                work_id: list(observation_ids.get(work_id, ()))
                for work_id in work_ids
            },
        }
        job = self._jobs.create(
            kind=RECOMMENDATION_JOB_KIND,
            payload=payload,
            idempotency_key=namespaced_key,
        )
        if job.kind != RECOMMENDATION_JOB_KIND or job.payload != payload:
            raise ValueError("idempotency key belongs to another recommendation command")
        return job

    async def execute(self, job_id: str) -> JobRecord:
        job = self._require_job(job_id)
        if job.status == "succeeded":
            return job
        if job.status not in {"queued", "running"}:
            raise ValueError("recommendation job must be queued before execution")
        checkpoint = self._checkpoint(job.checkpoint)
        if job.cancel_requested:
            return self._cancel(job, checkpoint)
        self._jobs.update_state(
            job.id,
            status="running",
            progress=self._progress(checkpoint, job.payload),
            checkpoint=checkpoint,
        )
        try:
            if not checkpoint["prepared"]:
                checkpoint["prepared"] = self._prepare(job.payload)
                self._jobs.update_state(
                    job.id,
                    status="running",
                    progress=10,
                    checkpoint=checkpoint,
                )
                self._jobs.append_event(
                    job_id=job.id,
                    event_type="recommendation.facts.prepared",
                    progress=10,
                    payload={"candidate_count": len(checkpoint["prepared"])},
                )

            for prepared in checkpoint["prepared"]:
                work_id = self._required_string(prepared, "work_id")
                if work_id in checkpoint["completed"]:
                    continue
                current = self._require_job(job.id)
                if current.cancel_requested:
                    return self._cancel(current, checkpoint)
                candidate_refs = self._persist_candidate(
                    job=job,
                    prepared=prepared,
                )
                checkpoint["completed"][work_id] = candidate_refs.by_type()
                progress = self._progress(checkpoint, job.payload)
                self._jobs.update_state(
                    job.id,
                    status="running",
                    progress=progress,
                    checkpoint=checkpoint,
                )
                self._jobs.append_event(
                    job_id=job.id,
                    event_type="recommendation.candidate.scored",
                    progress=progress,
                    payload={"work_id": work_id},
                )

            candidates = tuple(
                self._ranking_candidate(prepared)
                for prepared in checkpoint["prepared"]
            )
            snapshot_refs = {
                work_id: self._snapshot_refs(raw_refs)
                for work_id, raw_refs in checkpoint["completed"].items()
            }
            result = self._results.create(
                run_id=self._required_string(job.payload, "run_id"),
                candidates=candidates,
                snapshot_refs=snapshot_refs,
                fixed_historical_months=bool(
                    job.payload.get("fixed_historical_months")
                ),
                limit=self._required_int(job.payload, "limit"),
                candidate_cap=self._required_int(job.payload, "candidate_cap"),
            )
            summary = {
                "run_id": result.run_id,
                "recommendation_set_id": result.id,
                "recommendation_set_version": result.version,
                "candidate_count": len(candidates),
                "recommendation_count": result.recommendation_count,
            }
            completed = self._jobs.update_state(
                job.id,
                status="succeeded",
                progress=100,
                result=summary,
                checkpoint=checkpoint,
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="recommendation.run.succeeded",
                progress=100,
                payload=summary,
            )
            return completed
        except Exception:
            failed = self._jobs.update_state(
                job.id,
                status="failed",
                progress=self._progress(checkpoint, job.payload),
                checkpoint=checkpoint,
                error_code="recommendation_scoring_failed",
                error_message="Recommendation scoring could not complete",
            )
            self._jobs.append_event(
                job_id=job.id,
                event_type="recommendation.run.failed",
                progress=failed.progress,
                payload={
                    "run_id": job.payload.get("run_id"),
                    "completed": len(checkpoint["completed"]),
                },
            )
            return failed

    def resumable_job_ids(self) -> tuple[str, ...]:
        return self._jobs.list_resumable_ids(kind=RECOMMENDATION_JOB_KIND)

    def _prepare(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        run_id = self._required_string(payload, "run_id")
        work_ids = self._string_list(payload, "work_ids")
        relevance = self._relevance.score_run(run_id)
        if relevance.run_id != run_id:
            raise ValueError("relevance result belongs to a different run")
        if relevance.keyword_version_id != self._required_string(
            payload,
            "keyword_version_id",
        ):
            raise ValueError("relevance result uses a different keyword version")
        by_work = {candidate.work_id: candidate for candidate in relevance.candidates}
        if set(by_work) != set(work_ids):
            raise ValueError("relevance result must exactly cover frozen candidates")
        preferences = self._preferences.score_works(work_ids)
        if set(preferences) != set(work_ids):
            raise ValueError("preference result must exactly cover frozen candidates")
        rule = self._mapping(payload, "quality_rule")
        rule_version = self._required_string(rule, "rule_version")
        weights = self._numeric_mapping(rule, "weights")
        observation_manifest = self._mapping(payload, "observation_ids_by_work")
        scoring_at = self._parse_datetime(self._required_string(payload, "scoring_at"))
        fixed_historical = bool(payload.get("fixed_historical_months"))
        relevance_scores = [candidate.relevance.score for candidate in by_work.values()]
        relevance_median = median(relevance_scores) if relevance_scores else 0.0
        source_counts = Counter(
            source
            for candidate in by_work.values()
            for source in candidate.source_ids
        )
        primary_source = (
            min(
                source_counts,
                key=lambda source: (-source_counts[source], source),
            )
            if source_counts
            else None
        )

        prepared: list[dict[str, Any]] = []
        for work_id in work_ids:
            candidate = by_work[work_id]
            preference = preferences[work_id]
            observation_ids = self._string_sequence(
                observation_manifest.get(work_id, []),
                field=f"observation_ids_by_work.{work_id}",
            )
            quality = self._quality.build(
                work_id,
                observation_ids=observation_ids,
                rule_version=rule_version,
                weights=weights,
            )
            freshness = self._freshness(
                candidate.updated_at or candidate.published_at,
                scoring_at=scoring_at,
                fixed_historical_months=fixed_historical,
            )
            reasons = self._exploration_reasons(
                candidate_relevance=candidate.relevance.score,
                relevance_median=relevance_median,
                source_ids=candidate.source_ids,
                primary_source=primary_source,
                preference=preference,
            )
            quality_confidence = quality.integrity.constrain_confidence(
                quality.quality.confidence
            )
            ranking = RankingCandidate(
                work_id=work_id,
                relevance=RankingSignal(
                    candidate.relevance.score,
                    candidate.relevance.coverage,
                    candidate.relevance.confidence,
                    evidence={
                        "query": relevance.query,
                        "degradation_reasons": candidate.relevance.degradation_reasons,
                    },
                ),
                quality=RankingSignal(
                    quality.quality.score,
                    quality.quality.coverage,
                    quality_confidence,
                    evidence={
                        "observation_ids": quality.observation_ids,
                        "degradation_reasons": quality.degradation_reasons,
                    },
                ),
                freshness=freshness,
                preference=RankingSignal(
                    preference.score,
                    preference.coverage,
                    preference.confidence,
                    evidence=dict(preference.evidence),
                ),
                updated_at=candidate.updated_at or candidate.published_at,
                integrity=quality.integrity,
                keyword_eligible=candidate.eligible,
                preference_suppressed=preference.suppressed_by_default,
                historical_match=preference.historical_match,
                exploration_reasons=reasons,
            )
            prepared.append(
                self._prepared_candidate(
                    ranking=ranking,
                    relevance_result=candidate.relevance,
                    quality_result=quality,
                    preference_result=preference,
                    fixed_historical_months=fixed_historical,
                )
            )
        return cast(list[dict[str, Any]], self._json_copy(prepared))

    def _persist_candidate(
        self,
        *,
        job: JobRecord,
        prepared: Mapping[str, Any],
    ) -> CandidateSnapshotRefs:
        work_id = self._required_string(prepared, "work_id")
        drafts = self._mapping(prepared, "snapshot_drafts")
        context_key = f"recommendation-job:{job.id}"
        snapshots: dict[str, ScoreSnapshot] = {}
        for score_type in ("relevance", "quality", "freshness", "preference"):
            draft = self._mapping(drafts, score_type)
            snapshots[score_type] = self._snapshots.record(
                work_id=work_id,
                run_id=self._required_string(job.payload, "run_id"),
                context_key=context_key,
                score_type=cast(Any, score_type),
                rule_version=self._required_string(draft, "rule_version"),
                score=self._required_number(draft, "score"),
                coverage=self._required_number(draft, "coverage"),
                confidence=self._required_number(draft, "confidence"),
                components=self._mapping_sequence(draft, "components"),
                weights=self._numeric_mapping(draft, "weights"),
                missing_policy=self._mapping(draft, "missing_policy"),
                evidence_observation_ids=self._string_sequence(
                    draft.get("evidence_observation_ids", []),
                    field="evidence_observation_ids",
                ),
                hard_constraints=self._mapping(draft, "hard_constraints"),
            )

        ranking = self._ranking_candidate(prepared)
        composite = score_composite(
            ranking,
            fixed_historical_months=bool(
                job.payload.get("fixed_historical_months")
            ),
        )
        composite_snapshot = self._snapshots.record(
            work_id=work_id,
            run_id=self._required_string(job.payload, "run_id"),
            context_key=context_key,
            score_type="composite",
            rule_version=composite.rule_version,
            score=composite.score,
            coverage=composite.coverage,
            confidence=composite.confidence,
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
                for component in composite.components
            ),
            weights={
                component.dimension: component.effective_weight
                for component in composite.components
            },
            missing_policy={
                "unknown": "signal_specific_policy",
                "fixed_historical_freshness": "removed_and_redistributed",
            },
            evidence_observation_ids=self._string_sequence(
                self._mapping(drafts, "quality").get(
                    "evidence_observation_ids",
                    [],
                ),
                field="quality.evidence_observation_ids",
            ),
            hard_constraints=dict(composite.hard_constraints),
        )
        return CandidateSnapshotRefs(
            relevance_snapshot_id=snapshots["relevance"].id,
            quality_snapshot_id=snapshots["quality"].id,
            freshness_snapshot_id=snapshots["freshness"].id,
            preference_snapshot_id=snapshots["preference"].id,
            composite_snapshot_id=composite_snapshot.id,
        )

    @classmethod
    def _prepared_candidate(
        cls,
        *,
        ranking: RankingCandidate,
        relevance_result: Any,
        quality_result: QualityEvidence,
        preference_result: PreferenceScore,
        fixed_historical_months: bool,
    ) -> dict[str, Any]:
        freshness_reason = (
            "fixed_historical_months"
            if fixed_historical_months
            else ranking.freshness.evidence.get("reason")
        )
        return {
            "work_id": ranking.work_id,
            "updated_at": ranking.updated_at.isoformat() if ranking.updated_at else None,
            "keyword_eligible": ranking.keyword_eligible,
            "preference_suppressed": ranking.preference_suppressed,
            "historical_match": ranking.historical_match,
            "exploration_reasons": [reason.value for reason in ranking.exploration_reasons],
            "integrity": ranking.integrity.to_hard_constraints(),
            "signals": {
                name: {
                    "score": signal.score,
                    "coverage": signal.coverage,
                    "confidence": signal.confidence,
                    "evidence": dict(signal.evidence),
                }
                for name, signal in (
                    ("relevance", ranking.relevance),
                    ("quality", ranking.quality),
                    ("freshness", ranking.freshness),
                    ("preference", ranking.preference),
                )
            },
            "snapshot_drafts": {
                "relevance": {
                    "rule_version": relevance_result.rule_version,
                    "score": ranking.relevance.score,
                    "coverage": ranking.relevance.coverage,
                    "confidence": ranking.relevance.confidence,
                    "components": [
                        {
                            "dimension": component.dimension,
                            "configured_weight": component.configured_weight,
                            "effective_weight": component.effective_weight,
                            "status": component.status.value,
                            "value": component.value,
                            "confidence": component.confidence,
                            "reason": component.reason,
                            "evidence": dict(component.evidence),
                        }
                        for component in relevance_result.components
                    ],
                    "weights": {
                        component.dimension: component.configured_weight
                        for component in relevance_result.components
                    },
                    "missing_policy": {
                        "unknown": "excluded_and_observed_weights_renormalized"
                    },
                    "evidence_observation_ids": [],
                    "hard_constraints": {
                        "keyword_eligible": ranking.keyword_eligible,
                    },
                },
                "quality": {
                    "rule_version": quality_result.quality.rule_version,
                    "score": ranking.quality.score,
                    "coverage": ranking.quality.coverage,
                    "confidence": ranking.quality.confidence,
                    "components": [
                        {
                            "dimension": component.dimension,
                            "configured_weight": component.configured_weight,
                            "effective_weight": component.effective_weight,
                            "status": component.status.value,
                            "observed_value": component.observed_value,
                            "imputed_value": component.imputed_value,
                            "confidence": component.confidence,
                            "reason": component.reason,
                            "evidence": dict(component.evidence),
                        }
                        for component in quality_result.quality.components
                    ],
                    "weights": dict(quality_result.quality.weights),
                    "missing_policy": {
                        "unknown": "neutral_prior",
                        "neutral_value": 50.0,
                        "not_applicable": "removed_from_denominator",
                    },
                    "evidence_observation_ids": list(quality_result.observation_ids),
                    "hard_constraints": {
                        "integrity": quality_result.integrity.to_hard_constraints()
                    },
                },
                "freshness": {
                    "rule_version": FRESHNESS_RULE_VERSION,
                    "score": ranking.freshness.score,
                    "coverage": ranking.freshness.coverage,
                    "confidence": ranking.freshness.confidence,
                    "components": [
                        {
                            "dimension": "freshness",
                            "score": ranking.freshness.score,
                            "coverage": ranking.freshness.coverage,
                            "confidence": ranking.freshness.confidence,
                            "reason": freshness_reason,
                            "evidence": dict(ranking.freshness.evidence),
                        }
                    ],
                    "weights": {"freshness": 1.0},
                    "missing_policy": {"unknown": "neutral_prior"},
                    "evidence_observation_ids": [],
                    "hard_constraints": {},
                },
                "preference": {
                    "rule_version": preference_result.rule_version,
                    "score": ranking.preference.score,
                    "coverage": ranking.preference.coverage,
                    "confidence": ranking.preference.confidence,
                    "components": [
                        {
                            "dimension": "preference",
                            "score": ranking.preference.score,
                            "coverage": ranking.preference.coverage,
                            "confidence": ranking.preference.confidence,
                            "evidence": dict(preference_result.evidence),
                        }
                    ],
                    "weights": {"preference": 1.0},
                    "missing_policy": {"unknown": "neutral_prior"},
                    "evidence_observation_ids": [],
                    "hard_constraints": {
                        "suppressed_by_default": preference_result.suppressed_by_default,
                        "historical_match": preference_result.historical_match,
                    },
                },
            },
        }

    @classmethod
    def _ranking_candidate(cls, prepared: Mapping[str, Any]) -> RankingCandidate:
        signals = cls._mapping(prepared, "signals")
        updated_raw = prepared.get("updated_at")
        updated_at = (
            cls._parse_datetime(updated_raw)
            if isinstance(updated_raw, str)
            else None
        )
        return RankingCandidate(
            work_id=cls._required_string(prepared, "work_id"),
            relevance=cls._signal(cls._mapping(signals, "relevance")),
            quality=cls._signal(cls._mapping(signals, "quality")),
            freshness=cls._signal(cls._mapping(signals, "freshness")),
            preference=cls._signal(cls._mapping(signals, "preference")),
            updated_at=updated_at,
            integrity=cls._integrity(cls._mapping(prepared, "integrity")),
            keyword_eligible=bool(prepared.get("keyword_eligible")),
            preference_suppressed=bool(prepared.get("preference_suppressed")),
            historical_match=bool(prepared.get("historical_match")),
            exploration_reasons=tuple(
                ExplorationReason(value)
                for value in cls._string_sequence(
                    prepared.get("exploration_reasons", []),
                    field="exploration_reasons",
                )
            ),
        )

    @classmethod
    def _signal(cls, value: Mapping[str, Any]) -> RankingSignal:
        return RankingSignal(
            score=cls._required_number(value, "score"),
            coverage=cls._required_number(value, "coverage"),
            confidence=cls._required_number(value, "confidence"),
            evidence=cls._mapping(value, "evidence"),
        )

    @classmethod
    def _integrity(cls, value: Mapping[str, Any]) -> IntegrityAssessment:
        warnings = tuple(
            IntegrityWarning(
                code=cls._required_string(item, "code"),
                source=cls._required_string(item, "source"),
                evidence_path=cls._required_string(item, "evidence_path"),
                update_type=cls._optional_string(item.get("update_type")),
                notice_doi=cls._optional_string(item.get("notice_doi")),
                observed_at=cls._optional_string(item.get("observed_at")),
            )
            for item in cls._mapping_sequence(value, "warnings")
        )
        cap_raw = value.get("confidence_cap")
        cap = float(cap_raw) if isinstance(cap_raw, (int, float)) else None
        return IntegrityAssessment(
            rule_version=cls._required_string(value, "rule_version"),
            status=IntegrityStatus(cls._required_string(value, "status")),
            eligible_by_default=bool(value.get("eligible_by_default")),
            visible=bool(value.get("visible")),
            requires_strong_warning=bool(value.get("requires_strong_warning")),
            confidence_cap=cap,
            evidence_coverage=cls._required_number(value, "evidence_coverage"),
            warnings=warnings,
            observed_sources=cls._string_sequence(
                value.get("observed_sources", []),
                field="observed_sources",
            ),
        )

    @staticmethod
    def _freshness(
        updated_at: datetime | None,
        *,
        scoring_at: datetime,
        fixed_historical_months: bool,
    ) -> RankingSignal:
        if fixed_historical_months:
            return RankingSignal(
                50,
                0,
                0,
                evidence={
                    "rule_version": FRESHNESS_RULE_VERSION,
                    "reason": "fixed_historical_months",
                },
            )
        if updated_at is None:
            return RankingSignal(
                50,
                0,
                0,
                evidence={
                    "rule_version": FRESHNESS_RULE_VERSION,
                    "reason": "source_update_time_unknown",
                },
            )
        observed = RecommendationOrchestrator._aware(updated_at)
        age_days = max(0.0, (scoring_at - observed).total_seconds() / 86_400)
        score = 100.0 * math.pow(2.0, -age_days / 180.0)
        return RankingSignal(
            round(score, 6),
            100,
            100,
            evidence={
                "rule_version": FRESHNESS_RULE_VERSION,
                "updated_at": observed.isoformat(),
                "scoring_at": scoring_at.isoformat(),
                "age_days": round(age_days, 6),
                "half_life_days": 180,
            },
        )

    @staticmethod
    def _exploration_reasons(
        *,
        candidate_relevance: float,
        relevance_median: float,
        source_ids: Sequence[str],
        primary_source: str | None,
        preference: PreferenceScore,
    ) -> tuple[ExplorationReason, ...]:
        reasons: list[ExplorationReason] = []
        if candidate_relevance < relevance_median:
            reasons.append(ExplorationReason.ADJACENT_TOPIC)
        if not preference.matched_author_keys and not preference.historical_match:
            reasons.append(ExplorationReason.NEW_AUTHOR)
        if primary_source is not None and any(
            source != primary_source for source in source_ids
        ):
            reasons.append(ExplorationReason.SOURCE_DIVERSITY)
        return tuple(reasons)

    def _cancel(
        self,
        job: JobRecord,
        checkpoint: dict[str, Any],
    ) -> JobRecord:
        progress = self._progress(checkpoint, job.payload)
        cancelled = self._jobs.update_state(
            job.id,
            status="cancelled",
            progress=progress,
            checkpoint=checkpoint,
            result={
                "run_id": job.payload.get("run_id"),
                "completed": len(checkpoint["completed"]),
            },
            error_code="cancelled_by_user",
            error_message="Recommendation scoring was cancelled",
        )
        self._jobs.append_event(
            job_id=job.id,
            event_type="recommendation.run.cancelled",
            progress=progress,
            payload={"run_id": job.payload.get("run_id")},
        )
        return cancelled

    def _require_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind != RECOMMENDATION_JOB_KIND:
            raise ValueError("job is not a recommendation command")
        return job

    @staticmethod
    def _validate_existing_job(
        job: JobRecord,
        run_id: str,
        trigger: RecommendationTrigger,
    ) -> None:
        if (
            job.kind != RECOMMENDATION_JOB_KIND
            or job.payload.get("run_id") != run_id
            or job.payload.get("trigger") != trigger
        ):
            raise ValueError("idempotency key belongs to another recommendation command")

    @staticmethod
    def _idempotency_key(
        *,
        run_id: str,
        trigger: RecommendationTrigger,
        trigger_job_id: str | None,
        supplied: str | None,
    ) -> str | None:
        if supplied is not None:
            return f"recommendation.run:{supplied}"
        if trigger_job_id is not None:
            return f"recommendation.run:{run_id}:{trigger}:{trigger_job_id}"
        return None

    @staticmethod
    def _trigger(value: str) -> RecommendationTrigger:
        if value not in {"discovery", "metadata", "manual"}:
            raise ValueError("unknown recommendation trigger")
        return cast(RecommendationTrigger, value)

    @staticmethod
    def _fixed_historical_months(run: DiscoveryRun) -> bool:
        raw = run.plan_snapshot.get("date_range", {})
        return isinstance(raw, dict) and raw.get("mode") == "fixed"

    @staticmethod
    def _quota(run: DiscoveryRun) -> tuple[int, int]:
        raw = run.plan_snapshot.get("quota", {})
        if not isinstance(raw, dict):
            raw = {}
        limit = raw.get("priority_limit", 20)
        cap = raw.get("candidate_limit", 50)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("run priority limit is invalid")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < limit:
            raise ValueError("run candidate limit is invalid")
        return limit, cap

    @staticmethod
    def _checkpoint(value: Mapping[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {"prepared": [], "completed": {}}
        prepared = value.get("prepared", [])
        completed = value.get("completed", {})
        if not isinstance(prepared, list) or not all(
            isinstance(item, dict) for item in prepared
        ):
            raise ValueError("recommendation prepared checkpoint is invalid")
        if not isinstance(completed, dict) or not all(
            isinstance(key, str) and isinstance(item, dict)
            for key, item in completed.items()
        ):
            raise ValueError("recommendation completed checkpoint is invalid")
        return {
            "prepared": [dict(item) for item in prepared],
            "completed": {key: dict(item) for key, item in completed.items()},
        }

    @staticmethod
    def _progress(checkpoint: Mapping[str, Any], payload: Mapping[str, Any]) -> int:
        prepared = checkpoint.get("prepared", [])
        completed = checkpoint.get("completed", {})
        work_ids = payload.get("work_ids", [])
        if not isinstance(work_ids, list) or not work_ids:
            return 10 if prepared else 0
        prepared_share = 10 if prepared else 0
        completed_count = len(completed) if isinstance(completed, dict) else 0
        return min(99, prepared_share + int((completed_count * 89) / len(work_ids)))

    @staticmethod
    def _snapshot_refs(value: Mapping[str, Any]) -> CandidateSnapshotRefs:
        return CandidateSnapshotRefs(
            relevance_snapshot_id=RecommendationOrchestrator._required_string(
                value, "relevance"
            ),
            quality_snapshot_id=RecommendationOrchestrator._required_string(
                value, "quality"
            ),
            freshness_snapshot_id=RecommendationOrchestrator._required_string(
                value, "freshness"
            ),
            preference_snapshot_id=RecommendationOrchestrator._required_string(
                value, "preference"
            ),
            composite_snapshot_id=RecommendationOrchestrator._required_string(
                value, "composite"
            ),
        )

    @staticmethod
    def _mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
        selected = value.get(field)
        if not isinstance(selected, dict):
            raise ValueError(f"{field} must be an object")
        return dict(selected)

    @staticmethod
    def _mapping_sequence(
        value: Mapping[str, Any],
        field: str,
    ) -> tuple[dict[str, Any], ...]:
        selected = value.get(field)
        if not isinstance(selected, list) or not all(
            isinstance(item, dict) for item in selected
        ):
            raise ValueError(f"{field} must be an object list")
        return tuple(dict(item) for item in selected)

    @staticmethod
    def _numeric_mapping(value: Mapping[str, Any], field: str) -> dict[str, float]:
        selected = RecommendationOrchestrator._mapping(value, field)
        if any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            for item in selected.values()
        ):
            raise ValueError(f"{field} must contain finite numbers")
        return {key: float(item) for key, item in selected.items()}

    @staticmethod
    def _required_string(value: Mapping[str, Any], field: str) -> str:
        selected = value.get(field)
        if not isinstance(selected, str) or not selected.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return selected

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _required_number(value: Mapping[str, Any], field: str) -> float:
        selected = value.get(field)
        if (
            not isinstance(selected, (int, float))
            or isinstance(selected, bool)
            or not math.isfinite(float(selected))
        ):
            raise ValueError(f"{field} must be a finite number")
        return float(selected)

    @staticmethod
    def _required_int(value: Mapping[str, Any], field: str) -> int:
        selected = value.get(field)
        if not isinstance(selected, int) or isinstance(selected, bool) or selected < 1:
            raise ValueError(f"{field} must be a positive integer")
        return selected

    @staticmethod
    def _string_list(value: Mapping[str, Any], field: str) -> tuple[str, ...]:
        return RecommendationOrchestrator._string_sequence(
            value.get(field),
            field=field,
        )

    @staticmethod
    def _string_sequence(value: object, *, field: str) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise ValueError(f"{field} must be a string list")
        return tuple(value)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return RecommendationOrchestrator._aware(parsed)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recommendation timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _json_copy(value: Any) -> Any:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )


def build_recommendation_orchestrator(
    *,
    runs: DiscoveryRunRepository,
    jobs: JobRepository,
    relevance: RunRelevanceScorer,
    quality: QualityEvidenceBuilder,
    preferences: PreferenceRepository,
    rules: ScoreRuleRepository,
    snapshots: ScoreSnapshotRepository,
    results: RecommendationSetRepository,
) -> RecommendationOrchestrator:
    """Typed construction helper used by the application runtime."""

    return RecommendationOrchestrator(
        runs=runs,
        jobs=jobs,
        relevance=relevance,
        quality=quality,
        preferences=preferences,
        rules=rules,
        snapshots=snapshots,
        results=results,
    )
