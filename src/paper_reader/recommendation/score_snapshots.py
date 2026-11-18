"""Immutable score snapshot persistence and reproducibility metadata."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import PlanRunRecord, ScoreSnapshotRecord, WorkRecord
from paper_reader.jobs.models import utc_now
from paper_reader.recommendation.integrity import IntegrityAssessment
from paper_reader.recommendation.quality import QualityScore

ScoreType = Literal["relevance", "quality", "freshness", "preference", "composite"]
SCORE_TYPES: frozenset[str] = frozenset(
    {"relevance", "quality", "freshness", "preference", "composite"}
)


@dataclass(frozen=True, slots=True)
class ScoreSnapshot:
    id: str
    work_id: str
    run_id: str | None
    context_key: str
    score_type: ScoreType
    rule_version: str
    score: float
    coverage: float
    confidence: float
    components: tuple[dict[str, Any], ...]
    weights: dict[str, float]
    missing_policy: dict[str, Any]
    evidence_observation_ids: tuple[str, ...]
    hard_constraints: dict[str, Any]
    snapshot_sha256: str
    created_at: datetime


class ScoreSnapshotRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record_quality(
        self,
        *,
        work_id: str,
        context_key: str,
        quality: QualityScore,
        evidence_observation_ids: tuple[str, ...],
        run_id: str | None = None,
        hard_constraints: dict[str, Any] | None = None,
        integrity: IntegrityAssessment | None = None,
    ) -> ScoreSnapshot:
        components = tuple(
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
            for component in quality.components
        )
        weights = dict(quality.weights)
        missing_policy: dict[str, Any] = {
            "unknown": "neutral_prior",
            "neutral_value": 50.0,
            "not_applicable": "removed_from_denominator",
        }
        constraints = dict(hard_constraints or {})
        if integrity is not None:
            if "integrity" in constraints:
                raise ValueError("integrity hard constraint must not be overridden")
            constraints["integrity"] = integrity.to_hard_constraints()
        confidence = (
            integrity.constrain_confidence(quality.confidence)
            if integrity is not None
            else quality.confidence
        )
        return self.record(
            work_id=work_id,
            run_id=run_id,
            context_key=context_key,
            score_type="quality",
            rule_version=quality.rule_version,
            score=quality.score,
            coverage=quality.coverage,
            confidence=confidence,
            components=components,
            weights=weights,
            missing_policy=missing_policy,
            evidence_observation_ids=evidence_observation_ids,
            hard_constraints=constraints,
        )

    def record(
        self,
        *,
        work_id: str,
        context_key: str,
        score_type: ScoreType,
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
    ) -> ScoreSnapshot:
        """Record one closed score kind as an immutable, content-addressed fact."""

        context = context_key.strip()
        if not context:
            raise ValueError("context_key must not be blank")
        selected_type = str(score_type)
        if selected_type not in SCORE_TYPES:
            raise ValueError("score_type must be a supported closed score type")
        version = rule_version.strip()
        if not version:
            raise ValueError("rule_version must not be blank")
        numeric_values = {
            "score": float(score),
            "coverage": float(coverage),
            "confidence": float(confidence),
        }
        for name, value in numeric_values.items():
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")

        evidence_ids = tuple(evidence_observation_ids)
        if any(
            not isinstance(evidence_id, str) or not evidence_id.strip()
            for evidence_id in evidence_ids
        ):
            raise ValueError("evidence observation IDs must not be blank")
        evidence_ids = tuple(evidence_id.strip() for evidence_id in evidence_ids)
        normalized_components = self._json_copy(tuple(dict(item) for item in components))
        normalized_weights = self._json_copy(dict(weights))
        if any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0
            for name, value in normalized_weights.items()
        ):
            raise ValueError("weights must contain finite non-negative numeric values")
        normalized_missing_policy = self._json_copy(dict(missing_policy))
        normalized_constraints = self._json_copy(dict(hard_constraints or {}))
        payload = {
            "run_id": run_id,
            "score_type": selected_type,
            "rule_version": version,
            **numeric_values,
            "components": normalized_components,
            "weights": normalized_weights,
            "missing_policy": normalized_missing_policy,
            "evidence_observation_ids": evidence_ids,
            "hard_constraints": normalized_constraints,
        }
        fingerprint = self._sha256(payload)

        with self._sessions() as session:
            work = session.get(WorkRecord, work_id)
            if work is None:
                raise KeyError(work_id)
            if run_id is not None and session.get(PlanRunRecord, run_id) is None:
                raise KeyError(run_id)
            existing = session.scalar(
                select(ScoreSnapshotRecord).where(
                    ScoreSnapshotRecord.work_id == work_id,
                    ScoreSnapshotRecord.context_key == context,
                    ScoreSnapshotRecord.score_type == selected_type,
                    ScoreSnapshotRecord.snapshot_sha256 == fingerprint,
                )
            )
            if existing is not None:
                return self._domain(existing)
            record = ScoreSnapshotRecord(
                work_id=work_id,
                run_id=run_id,
                context_key=context,
                score_type=selected_type,
                rule_version=version,
                score=numeric_values["score"],
                coverage=numeric_values["coverage"],
                confidence=numeric_values["confidence"],
                components=normalized_components,
                weights=normalized_weights,
                missing_policy=normalized_missing_policy,
                evidence_observation_ids=list(evidence_ids),
                hard_constraints=normalized_constraints,
                snapshot_sha256=fingerprint,
                created_at=utc_now(),
            )
            session.add(record)
            session.commit()
            return self._domain(record)

    def latest(
        self,
        work_id: str,
        *,
        score_type: ScoreType,
        context_key: str,
    ) -> ScoreSnapshot | None:
        context = context_key.strip()
        if not context:
            raise ValueError("context_key must not be blank")
        if str(score_type) not in SCORE_TYPES:
            raise ValueError("score_type must be a supported closed score type")
        with self._sessions() as session:
            record = session.scalar(
                select(ScoreSnapshotRecord)
                .where(
                    ScoreSnapshotRecord.work_id == work_id,
                    ScoreSnapshotRecord.score_type == score_type,
                    ScoreSnapshotRecord.context_key == context,
                )
                .order_by(
                    ScoreSnapshotRecord.created_at.desc(),
                    ScoreSnapshotRecord.id.desc(),
                )
            )
            return self._domain(record) if record is not None else None

    def get(self, snapshot_id: str) -> ScoreSnapshot:
        with self._sessions() as session:
            record = session.get(ScoreSnapshotRecord, snapshot_id)
            if record is None:
                raise KeyError(snapshot_id)
            return self._domain(record)

    @staticmethod
    def _sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _json_copy(value: Any) -> Any:
        try:
            return json.loads(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("score evidence must be JSON serializable") from error

    @classmethod
    def _domain(cls, record: ScoreSnapshotRecord) -> ScoreSnapshot:
        created_at = record.created_at
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        return ScoreSnapshot(
            id=record.id,
            work_id=record.work_id,
            run_id=record.run_id,
            context_key=record.context_key,
            score_type=record.score_type,  # type: ignore[arg-type]
            rule_version=record.rule_version,
            score=record.score,
            coverage=record.coverage,
            confidence=record.confidence,
            components=tuple(dict(component) for component in record.components),
            weights=dict(record.weights),
            missing_policy=dict(record.missing_policy),
            evidence_observation_ids=tuple(record.evidence_observation_ids),
            hard_constraints=dict(record.hard_constraints),
            snapshot_sha256=record.snapshot_sha256,
            created_at=created_at,
        )
