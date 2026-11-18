"""Immutable recommendation result sets built only from persisted score facts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import batched
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    PlanRunRecord,
    RecommendationItemRecord,
    RecommendationSetRecord,
    ScoreSnapshotRecord,
)
from paper_reader.jobs.models import utc_now
from paper_reader.recommendation.ranking import (
    COMPOSITE_RULE_VERSION,
    DEFAULT_RECOMMENDATION_LIMIT,
    MAX_CANDIDATE_CAP,
    ExplorationReason,
    RankingCandidate,
    RankingView,
    RecommendationChannel,
    build_recommendation_batch,
    rank_candidates,
    ranking_view_sort_key,
    score_composite,
)


@dataclass(frozen=True, slots=True)
class CandidateSnapshotRefs:
    relevance_snapshot_id: str
    quality_snapshot_id: str
    freshness_snapshot_id: str
    preference_snapshot_id: str
    composite_snapshot_id: str

    def by_type(self) -> dict[str, str]:
        return {
            "relevance": self.relevance_snapshot_id,
            "quality": self.quality_snapshot_id,
            "freshness": self.freshness_snapshot_id,
            "preference": self.preference_snapshot_id,
            "composite": self.composite_snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class RecommendationItem:
    id: str
    scored_work_id: str
    default_position: int | None
    selected: bool
    historical_match: bool
    eligible_by_default: bool
    channel: RecommendationChannel | None
    ineligibility_reasons: tuple[str, ...]
    exploration_reasons: tuple[ExplorationReason, ...]
    source_updated_at: datetime | None
    snapshots: CandidateSnapshotRefs
    ranking_evidence: dict[str, Any]
    item_sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecommendationSet:
    id: str
    run_id: str
    keyword_version_id: str
    version: int
    ranking_rule_version: str
    fixed_historical_months: bool
    requested_limit: int
    candidate_cap: int
    candidate_count: int
    recommendation_count: int
    exploration_target: int
    exploration_count: int
    degradation_reasons: tuple[str, ...]
    set_sha256: str
    created_at: datetime
    items: tuple[RecommendationItem, ...]


@dataclass(frozen=True, slots=True)
class RecommendationViewResult:
    recommendation_set_id: str
    recommendation_set_version: int
    view: RankingView
    items: tuple[RecommendationItem, ...]
    historical_matches: tuple[RecommendationItem, ...]


class RecommendationSetRepository:
    """Store one append-only result set for each materially different scoring pass."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(
        self,
        *,
        run_id: str,
        candidates: Sequence[RankingCandidate],
        snapshot_refs: Mapping[str, CandidateSnapshotRefs],
        fixed_historical_months: bool,
        limit: int = DEFAULT_RECOMMENDATION_LIMIT,
        candidate_cap: int = MAX_CANDIDATE_CAP,
        ranking_rule_version: str = COMPOSITE_RULE_VERSION,
    ) -> RecommendationSet:
        rule_version = ranking_rule_version.strip()
        if not rule_version:
            raise ValueError("ranking_rule_version must not be blank")
        candidate_tuple = tuple(candidates)
        candidate_by_id = {candidate.work_id: candidate for candidate in candidate_tuple}
        if len(candidate_by_id) != len(candidate_tuple):
            raise ValueError("candidate work IDs must be unique")
        if set(snapshot_refs) != set(candidate_by_id):
            raise ValueError("snapshot_refs must exactly match candidate work IDs")

        batch = build_recommendation_batch(
            candidate_tuple,
            fixed_historical_months=fixed_historical_months,
            limit=limit,
            candidate_cap=candidate_cap,
        )
        ranked_eligible = rank_candidates(
            candidate_tuple,
            view=RankingView.COMPOSITE,
            fixed_historical_months=fixed_historical_months,
        )
        eligible_new = [
            item for item in ranked_eligible if not item.candidate.historical_match
        ][:candidate_cap]
        all_ranked = rank_candidates(
            candidate_tuple,
            view=RankingView.COMPOSITE,
            fixed_historical_months=fixed_historical_months,
            include_ineligible=True,
        )
        ineligible_new = [
            item
            for item in all_ranked
            if not item.candidate.historical_match and not item.eligible_by_default
        ]
        available_ineligible_slots = max(0, candidate_cap - len(eligible_new))
        persisted_new_ids = {
            item.work_id
            for item in (
                *eligible_new,
                *ineligible_new[:available_ineligible_slots],
            )
        }
        persisted_ranked = tuple(
            item
            for item in all_ranked
            if item.candidate.historical_match or item.work_id in persisted_new_ids
        )
        degradation_reasons = list(batch.degradation_reasons)
        non_historical_count = sum(
            not candidate.historical_match for candidate in candidate_tuple
        )
        if (
            non_historical_count > candidate_cap
            and "candidate_pool_capped" not in degradation_reasons
        ):
            degradation_reasons.append("candidate_pool_capped")

        with self._sessions() as session:
            run = session.get(PlanRunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            snapshot_records = self._load_and_validate_snapshots(
                session,
                run_id=run_id,
                candidates=candidate_by_id,
                snapshot_refs=snapshot_refs,
                fixed_historical_months=fixed_historical_months,
                ranking_rule_version=rule_version,
            )
            selected = {item.work_id: item for item in batch.items}
            item_payloads: list[dict[str, Any]] = []
            for ranked in persisted_ranked:
                paper = ranked.candidate
                refs = snapshot_refs[paper.work_id]
                chosen = selected.get(paper.work_id)
                evidence = self._ranking_evidence(
                    paper,
                    refs=refs,
                    snapshots=snapshot_records,
                )
                item_payload = {
                    "scored_work_id": paper.work_id,
                    "default_position": chosen.position if chosen is not None else None,
                    "selected": chosen is not None,
                    "historical_match": paper.historical_match,
                    "eligible_by_default": paper.eligible_by_default,
                    "channel": chosen.channel.value if chosen is not None else None,
                    "ineligibility_reasons": list(paper.ineligibility_reasons),
                    "exploration_reasons": [
                        reason.value
                        for reason in (
                            chosen.exploration_reasons
                            if chosen is not None
                            else paper.exploration_reasons
                        )
                    ],
                    "source_updated_at": (
                        paper.updated_at.isoformat() if paper.updated_at is not None else None
                    ),
                    **{
                        f"{score_type}_snapshot_id": snapshot_id
                        for score_type, snapshot_id in refs.by_type().items()
                    },
                    "ranking_evidence": evidence,
                }
                item_payload["item_sha256"] = self._sha256(item_payload)
                item_payloads.append(item_payload)

            set_payload = {
                "run_id": run.id,
                "keyword_version_id": run.keyword_version_id,
                "ranking_rule_version": rule_version,
                "fixed_historical_months": fixed_historical_months,
                "requested_limit": batch.requested_limit,
                "candidate_cap": batch.candidate_cap,
                "candidate_count": len(persisted_new_ids),
                "recommendation_count": len(batch.items),
                "exploration_target": batch.exploration_target,
                "exploration_count": batch.exploration_count,
                "degradation_reasons": degradation_reasons,
                "items": item_payloads,
            }
            set_sha256 = self._sha256(set_payload)
            existing = session.scalar(
                select(RecommendationSetRecord).where(
                    RecommendationSetRecord.run_id == run.id,
                    RecommendationSetRecord.set_sha256 == set_sha256,
                )
            )
            if existing is not None:
                return self._domain(session, existing)

            version = int(
                session.scalar(
                    select(func.max(RecommendationSetRecord.version)).where(
                        RecommendationSetRecord.run_id == run.id
                    )
                )
                or 0
            ) + 1
            created_at = utc_now()
            record = RecommendationSetRecord(
                run_id=run.id,
                keyword_version_id=run.keyword_version_id,
                version=version,
                ranking_rule_version=rule_version,
                fixed_historical_months=fixed_historical_months,
                requested_limit=batch.requested_limit,
                candidate_cap=batch.candidate_cap,
                candidate_count=len(persisted_new_ids),
                recommendation_count=len(batch.items),
                exploration_target=batch.exploration_target,
                exploration_count=batch.exploration_count,
                degradation_reasons=degradation_reasons,
                set_sha256=set_sha256,
                created_at=created_at,
            )
            session.add(record)
            session.flush()
            session.add_all(
                RecommendationItemRecord(
                    recommendation_set_id=record.id,
                    scored_work_id=payload["scored_work_id"],
                    default_position=payload["default_position"],
                    selected=payload["selected"],
                    historical_match=payload["historical_match"],
                    eligible_by_default=payload["eligible_by_default"],
                    channel=payload["channel"],
                    ineligibility_reasons=payload["ineligibility_reasons"],
                    exploration_reasons=payload["exploration_reasons"],
                    source_updated_at=self._parse_optional_datetime(
                        payload["source_updated_at"]
                    ),
                    relevance_snapshot_id=payload["relevance_snapshot_id"],
                    quality_snapshot_id=payload["quality_snapshot_id"],
                    freshness_snapshot_id=payload["freshness_snapshot_id"],
                    preference_snapshot_id=payload["preference_snapshot_id"],
                    composite_snapshot_id=payload["composite_snapshot_id"],
                    ranking_evidence=payload["ranking_evidence"],
                    item_sha256=payload["item_sha256"],
                    created_at=created_at,
                )
                for payload in item_payloads
            )
            session.commit()
            return self._domain(session, record)

    def get(self, recommendation_set_id: str) -> RecommendationSet:
        with self._sessions() as session:
            record = session.get(RecommendationSetRecord, recommendation_set_id)
            if record is None:
                raise KeyError(recommendation_set_id)
            return self._domain(session, record)

    def latest_for_run(self, run_id: str) -> RecommendationSet | None:
        with self._sessions() as session:
            record = session.scalar(
                select(RecommendationSetRecord)
                .where(RecommendationSetRecord.run_id == run_id)
                .order_by(RecommendationSetRecord.version.desc())
            )
            return self._domain(session, record) if record is not None else None

    def selected_for_sets(self, set_ids: Sequence[str]) -> dict[str, list[RecommendationItem]]:
        selected: dict[str, list[RecommendationItem]] = {set_id: [] for set_id in set_ids}
        with self._sessions() as session:
            for batch in batched(selected, 500):
                records = session.scalars(
                    select(RecommendationItemRecord)
                    .where(
                        RecommendationItemRecord.recommendation_set_id.in_(batch),
                        RecommendationItemRecord.selected.is_(True),
                        RecommendationItemRecord.historical_match.is_(False),
                    )
                    .order_by(
                        RecommendationItemRecord.recommendation_set_id,
                        RecommendationItemRecord.default_position,
                        RecommendationItemRecord.scored_work_id,
                    )
                )
                for record in records:
                    selected[record.recommendation_set_id].append(self._item_domain(record))
        return selected

    def view(
        self,
        recommendation_set_id: str,
        *,
        view: RankingView,
        include_ineligible: bool = False,
    ) -> RecommendationViewResult:
        result = self.get(recommendation_set_id)
        selected_view = RankingView(view)
        visible = [
            item
            for item in result.items
            if not item.historical_match
            and (item.selected or (include_ineligible and not item.eligible_by_default))
        ]
        historical = [
            item
            for item in result.items
            if item.historical_match
            and (include_ineligible or item.eligible_by_default)
        ]
        if selected_view is RankingView.COMPOSITE and not include_ineligible:
            visible.sort(
                key=lambda item: (
                    item.default_position is None,
                    item.default_position or math.inf,
                    item.scored_work_id,
                )
            )
        else:
            visible.sort(key=lambda item: self._view_sort_key(item, selected_view))
        historical.sort(key=lambda item: self._view_sort_key(item, selected_view))
        return RecommendationViewResult(
            recommendation_set_id=result.id,
            recommendation_set_version=result.version,
            view=selected_view,
            items=tuple(visible),
            historical_matches=tuple(historical),
        )

    @classmethod
    def _load_and_validate_snapshots(
        cls,
        session: Session,
        *,
        run_id: str,
        candidates: Mapping[str, RankingCandidate],
        snapshot_refs: Mapping[str, CandidateSnapshotRefs],
        fixed_historical_months: bool,
        ranking_rule_version: str,
    ) -> dict[str, ScoreSnapshotRecord]:
        all_ids = [
            snapshot_id
            for refs in snapshot_refs.values()
            for snapshot_id in refs.by_type().values()
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("each candidate score type requires a distinct snapshot")
        records = {
            record.id: record
            for record in session.scalars(
                select(ScoreSnapshotRecord).where(ScoreSnapshotRecord.id.in_(all_ids))
            )
        }
        missing = set(all_ids) - set(records)
        if missing:
            raise KeyError(sorted(missing)[0])

        for work_id, paper in candidates.items():
            refs = snapshot_refs[work_id]
            expected_signals = {
                "relevance": paper.relevance,
                "quality": paper.quality,
                "freshness": paper.freshness,
                "preference": paper.preference,
            }
            for score_type, snapshot_id in refs.by_type().items():
                record = records[snapshot_id]
                if record.work_id != work_id:
                    raise ValueError("score snapshot belongs to a different Work")
                if record.run_id != run_id:
                    raise ValueError("score snapshot belongs to a different run")
                if record.score_type != score_type:
                    raise ValueError("score snapshot type does not match its reference")
                if score_type != "composite":
                    signal = expected_signals[score_type]
                    cls._require_same_measure(record, signal)

            composite = score_composite(
                paper,
                fixed_historical_months=fixed_historical_months,
            )
            composite_record = records[refs.composite_snapshot_id]
            cls._require_same_measure(composite_record, composite)
            if composite_record.rule_version != ranking_rule_version:
                raise ValueError("composite snapshot rule version does not match ranking")
            if composite_record.hard_constraints != dict(composite.hard_constraints):
                raise ValueError("composite snapshot constraints do not match ranking")
        return records

    @staticmethod
    def _require_same_measure(record: ScoreSnapshotRecord, expected: Any) -> None:
        for field_name in ("score", "coverage", "confidence"):
            if not math.isclose(
                float(getattr(record, field_name)),
                float(getattr(expected, field_name)),
                rel_tol=0,
                abs_tol=1e-6,
            ):
                raise ValueError(
                    f"score snapshot {field_name} does not match ranking candidate"
                )

    @staticmethod
    def _ranking_evidence(
        paper: RankingCandidate,
        *,
        refs: CandidateSnapshotRefs,
        snapshots: Mapping[str, ScoreSnapshotRecord],
    ) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        for score_type, snapshot_id in refs.by_type().items():
            snapshot = snapshots[snapshot_id]
            signals[score_type] = {
                "snapshot_id": snapshot.id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "rule_version": snapshot.rule_version,
                "score": snapshot.score,
                "coverage": snapshot.coverage,
                "confidence": snapshot.confidence,
            }
        composite = snapshots[refs.composite_snapshot_id]
        return {
            "signals": signals,
            "composite_components": composite.components,
            "hard_constraints": composite.hard_constraints,
            "keyword_eligible": paper.keyword_eligible,
            "preference_suppressed": paper.preference_suppressed,
            "historical_match": paper.historical_match,
            "eligible_by_default": paper.eligible_by_default,
            "ineligibility_reasons": list(paper.ineligibility_reasons),
            "updated_at": paper.updated_at.isoformat() if paper.updated_at else None,
        }

    @classmethod
    def _domain(
        cls,
        session: Session,
        record: RecommendationSetRecord,
    ) -> RecommendationSet:
        item_records = tuple(
            session.scalars(
                select(RecommendationItemRecord)
                .where(RecommendationItemRecord.recommendation_set_id == record.id)
                .order_by(
                    RecommendationItemRecord.historical_match,
                    RecommendationItemRecord.selected.desc(),
                    RecommendationItemRecord.default_position,
                    RecommendationItemRecord.scored_work_id,
                )
            )
        )
        return RecommendationSet(
            id=record.id,
            run_id=record.run_id,
            keyword_version_id=record.keyword_version_id,
            version=record.version,
            ranking_rule_version=record.ranking_rule_version,
            fixed_historical_months=record.fixed_historical_months,
            requested_limit=record.requested_limit,
            candidate_cap=record.candidate_cap,
            candidate_count=record.candidate_count,
            recommendation_count=record.recommendation_count,
            exploration_target=record.exploration_target,
            exploration_count=record.exploration_count,
            degradation_reasons=tuple(record.degradation_reasons),
            set_sha256=record.set_sha256,
            created_at=cls._utc(record.created_at),
            items=tuple(cls._item_domain(item) for item in item_records),
        )

    @classmethod
    def _item_domain(cls, record: RecommendationItemRecord) -> RecommendationItem:
        return RecommendationItem(
            id=record.id,
            scored_work_id=record.scored_work_id,
            default_position=record.default_position,
            selected=record.selected,
            historical_match=record.historical_match,
            eligible_by_default=record.eligible_by_default,
            channel=(
                RecommendationChannel(record.channel)
                if record.channel is not None
                else None
            ),
            ineligibility_reasons=tuple(record.ineligibility_reasons),
            exploration_reasons=tuple(
                ExplorationReason(reason) for reason in record.exploration_reasons
            ),
            source_updated_at=(
                cls._utc(record.source_updated_at)
                if record.source_updated_at is not None
                else None
            ),
            snapshots=CandidateSnapshotRefs(
                relevance_snapshot_id=record.relevance_snapshot_id,
                quality_snapshot_id=record.quality_snapshot_id,
                freshness_snapshot_id=record.freshness_snapshot_id,
                preference_snapshot_id=record.preference_snapshot_id,
                composite_snapshot_id=record.composite_snapshot_id,
            ),
            ranking_evidence=dict(record.ranking_evidence),
            item_sha256=record.item_sha256,
            created_at=cls._utc(record.created_at),
        )

    @staticmethod
    def _view_sort_key(
        item: RecommendationItem,
        view: RankingView,
    ) -> tuple[float, float, float, float, str]:
        signals = item.ranking_evidence["signals"]
        composite = float(signals["composite"]["score"])
        relevance = float(signals["relevance"]["score"])
        quality = float(signals["quality"]["score"])
        preference = float(signals["preference"]["score"])
        return ranking_view_sort_key(
            view=view,
            composite_score=composite,
            relevance_score=relevance,
            quality_score=quality,
            preference_score=preference,
            updated_at=item.source_updated_at,
            work_id=item.scored_work_id,
        )

    @staticmethod
    def _sha256(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _parse_optional_datetime(cls, value: str | None) -> datetime | None:
        return cls._utc(datetime.fromisoformat(value)) if value is not None else None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
