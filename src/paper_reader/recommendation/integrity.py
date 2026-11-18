"""Publication-integrity guardrails that cannot be offset by paper scores."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from paper_reader.recommendation.enrichment import EnrichmentResult, EnrichmentStatus

INTEGRITY_RULE_VERSION = "integrity-v1"
_SUPPORTED_SOURCES = frozenset({"openalex", "crossref"})
_RETRACTION_TYPES = frozenset({"retraction", "withdrawal", "removal"})
_CONCERN_TYPES = frozenset({"expression_of_concern", "partial_retraction"})
_CORRECTION_TYPES = frozenset(
    {"correction", "corrigendum", "erratum", "clarification", "addendum"}
)


class IntegrityStatus(StrEnum):
    UNKNOWN = "unknown"
    NO_KNOWN_ISSUE = "no_known_issue"
    MAJOR_CORRECTION = "major_correction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    REINSTATED = "reinstated"
    RETRACTED = "retracted"
    CONFLICTED = "conflicted"


@dataclass(frozen=True, slots=True)
class IntegrityWarning:
    code: str
    source: str
    evidence_path: str
    update_type: str | None = None
    notice_doi: str | None = None
    observed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source": self.source,
            "evidence_path": self.evidence_path,
            "update_type": self.update_type,
            "notice_doi": self.notice_doi,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class IntegrityAssessment:
    rule_version: str
    status: IntegrityStatus
    eligible_by_default: bool
    visible: bool
    requires_strong_warning: bool
    confidence_cap: float | None
    evidence_coverage: float
    warnings: tuple[IntegrityWarning, ...]
    observed_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if not math.isfinite(self.evidence_coverage) or not 0 <= self.evidence_coverage <= 100:
            raise ValueError("evidence_coverage must be between 0 and 100")
        if self.confidence_cap is not None and (
            not math.isfinite(self.confidence_cap) or not 0 <= self.confidence_cap <= 100
        ):
            raise ValueError("confidence_cap must be between 0 and 100")

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(warning.code for warning in self.warnings)

    def constrain_confidence(self, confidence: float) -> float:
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")
        if self.confidence_cap is None:
            return float(confidence)
        return float(min(confidence, self.confidence_cap))

    def to_hard_constraints(self) -> dict[str, Any]:
        return {
            "rule_version": self.rule_version,
            "status": self.status.value,
            "eligible_by_default": self.eligible_by_default,
            "visible": self.visible,
            "requires_strong_warning": self.requires_strong_warning,
            "confidence_cap": self.confidence_cap,
            "evidence_coverage": self.evidence_coverage,
            "observed_sources": list(self.observed_sources),
            "warnings": [warning.as_dict() for warning in self.warnings],
        }


@dataclass(frozen=True, slots=True)
class _UpdateEvent:
    update_type: str
    source: str
    evidence_path: str
    notice_doi: str | None
    observed_at: datetime | None

    def warning(self, code: str) -> IntegrityWarning:
        return IntegrityWarning(
            code=code,
            source=self.source,
            evidence_path=self.evidence_path,
            update_type=self.update_type,
            notice_doi=self.notice_doi,
            observed_at=self.observed_at.isoformat() if self.observed_at is not None else None,
        )


def assess_integrity(results: Iterable[EnrichmentResult]) -> IntegrityAssessment:
    """Assess only the latest observed result from each supported official source."""

    latest = _latest_supported_results(results)
    openalex_result = latest.get("openalex")
    crossref_result = latest.get("crossref")
    openalex_retracted = _openalex_retracted(openalex_result)
    crossref_observed = (
        crossref_result is not None
        and isinstance(crossref_result.fields.get("updates"), list)
    )
    observed_sources = tuple(
        source
        for source, observed in (
            ("crossref", crossref_observed),
            ("openalex", openalex_retracted is not None),
        )
        if observed
    )
    coverage = 100.0 * len(observed_sources) / len(_SUPPORTED_SOURCES)
    updates = _crossref_updates(crossref_result)
    crossref_state, primary_event = _crossref_state(updates)

    if crossref_state is IntegrityStatus.REINSTATED and openalex_retracted is True:
        return _assessment(
            IntegrityStatus.CONFLICTED,
            coverage,
            observed_sources,
            warning=IntegrityWarning(
                code="integrity_metadata_conflict",
                source="openalex+crossref",
                evidence_path="openalex:is_retracted|crossref:update-to",
                update_type="reinstatement",
                notice_doi=primary_event.notice_doi if primary_event is not None else None,
                observed_at=(
                    primary_event.observed_at.isoformat()
                    if primary_event is not None and primary_event.observed_at is not None
                    else None
                ),
            ),
        )
    if openalex_retracted is True or crossref_state is IntegrityStatus.RETRACTED:
        warning = (
            primary_event.warning("retracted")
            if crossref_state is IntegrityStatus.RETRACTED and primary_event is not None
            else IntegrityWarning(
                code="retracted",
                source="openalex",
                evidence_path="openalex:is_retracted",
            )
        )
        return _assessment(
            IntegrityStatus.RETRACTED,
            coverage,
            observed_sources,
            warning=warning,
        )
    if crossref_state is IntegrityStatus.EXPRESSION_OF_CONCERN:
        assert primary_event is not None
        return _assessment(
            crossref_state,
            coverage,
            observed_sources,
            warning=primary_event.warning("expression_of_concern"),
        )
    if crossref_state is IntegrityStatus.MAJOR_CORRECTION:
        assert primary_event is not None
        return _assessment(
            crossref_state,
            coverage,
            observed_sources,
            warning=primary_event.warning("major_correction"),
        )
    if crossref_state is IntegrityStatus.REINSTATED:
        assert primary_event is not None
        return _assessment(
            crossref_state,
            coverage,
            observed_sources,
            warning=primary_event.warning("reinstated"),
        )
    status = IntegrityStatus.NO_KNOWN_ISSUE if observed_sources else IntegrityStatus.UNKNOWN
    return _assessment(status, coverage, observed_sources)


def _assessment(
    status: IntegrityStatus,
    coverage: float,
    observed_sources: tuple[str, ...],
    *,
    warning: IntegrityWarning | None = None,
) -> IntegrityAssessment:
    confidence_caps: Mapping[IntegrityStatus, float] = {
        IntegrityStatus.MAJOR_CORRECTION: 70.0,
        IntegrityStatus.REINSTATED: 70.0,
        IntegrityStatus.EXPRESSION_OF_CONCERN: 40.0,
        IntegrityStatus.CONFLICTED: 40.0,
    }
    blocked = status in {IntegrityStatus.RETRACTED, IntegrityStatus.CONFLICTED}
    return IntegrityAssessment(
        rule_version=INTEGRITY_RULE_VERSION,
        status=status,
        eligible_by_default=not blocked,
        visible=True,
        requires_strong_warning=warning is not None,
        confidence_cap=confidence_caps.get(status),
        evidence_coverage=round(coverage, 6),
        warnings=(warning,) if warning is not None else (),
        observed_sources=observed_sources,
    )


def _latest_supported_results(
    results: Iterable[EnrichmentResult],
) -> dict[str, EnrichmentResult]:
    latest: dict[str, EnrichmentResult] = {}
    for result in results:
        if (
            result.enricher_id not in _SUPPORTED_SOURCES
            or result.status is not EnrichmentStatus.OBSERVED
        ):
            continue
        previous = latest.get(result.enricher_id)
        if previous is None or result.fetched_at > previous.fetched_at:
            latest[result.enricher_id] = result
    return latest


def _openalex_retracted(result: EnrichmentResult | None) -> bool | None:
    if result is None:
        return None
    value = result.fields.get("is_retracted")
    return value if isinstance(value, bool) else None


def _crossref_updates(result: EnrichmentResult | None) -> tuple[_UpdateEvent, ...]:
    if result is None:
        return ()
    raw_updates = result.fields.get("updates")
    if not isinstance(raw_updates, list):
        return ()
    base_path = result.field_provenance.get("updates", "crossref:update-to")
    updates: list[_UpdateEvent] = []
    for index, raw in enumerate(raw_updates):
        if not isinstance(raw, dict):
            continue
        update_type = _normalize_update_type(raw.get("type"))
        if update_type is None:
            continue
        notice_doi = raw.get("DOI")
        updates.append(
            _UpdateEvent(
                update_type=update_type,
                source="crossref",
                evidence_path=f"{base_path}[{index}]",
                notice_doi=notice_doi if isinstance(notice_doi, str) else None,
                observed_at=_update_datetime(raw.get("updated")),
            )
        )
    return tuple(updates)


def _crossref_state(
    updates: tuple[_UpdateEvent, ...],
) -> tuple[IntegrityStatus | None, _UpdateEvent | None]:
    retractions = tuple(event for event in updates if event.update_type in _RETRACTION_TYPES)
    reinstatements = tuple(event for event in updates if event.update_type == "reinstatement")
    if retractions:
        latest_retraction = _latest_event(retractions)
        assert latest_retraction is not None
        latest_reinstatement = _latest_event(reinstatements)
        if (
            latest_reinstatement is not None
            and latest_retraction.observed_at is not None
            and latest_reinstatement.observed_at is not None
            and latest_reinstatement.observed_at > latest_retraction.observed_at
        ):
            return IntegrityStatus.REINSTATED, latest_reinstatement
        return IntegrityStatus.RETRACTED, latest_retraction
    concerns = tuple(event for event in updates if event.update_type in _CONCERN_TYPES)
    if concerns:
        return IntegrityStatus.EXPRESSION_OF_CONCERN, _latest_event(concerns)
    corrections = tuple(event for event in updates if event.update_type in _CORRECTION_TYPES)
    if corrections:
        return IntegrityStatus.MAJOR_CORRECTION, _latest_event(corrections)
    if reinstatements:
        return IntegrityStatus.REINSTATED, _latest_event(reinstatements)
    return None, None


def _latest_event(events: tuple[_UpdateEvent, ...]) -> _UpdateEvent | None:
    if not events:
        return None
    return max(
        events,
        key=lambda event: (
            event.observed_at is not None,
            event.observed_at or datetime.min.replace(tzinfo=UTC),
            event.evidence_path,
        ),
    )


def _normalize_update_type(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return "_".join(value.strip().casefold().replace("-", " ").split())


def _update_datetime(value: object) -> datetime | None:
    if not isinstance(value, dict):
        return None
    date_time = value.get("date-time")
    if isinstance(date_time, str):
        try:
            parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    date_parts = value.get("date-parts")
    if (
        isinstance(date_parts, list)
        and date_parts
        and isinstance(date_parts[0], list)
        and date_parts[0]
    ):
        parts = date_parts[0]
        try:
            return datetime(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 1,
                int(parts[2]) if len(parts) > 2 else 1,
                tzinfo=UTC,
            )
        except (TypeError, ValueError):
            return None
    return None
