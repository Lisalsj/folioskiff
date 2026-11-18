"""Explicit preferences and privacy-bounded implicit in-app behavior."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    ImplicitPreferenceEventRecord,
    InterestedAuthorRecord,
    LibraryStateRecord,
    PreferenceAggregateRecord,
    WorkRecord,
)
from paper_reader.jobs.models import utc_now

RAW_EVENT_RETENTION_DAYS = 180
PREFERENCE_RULE_VERSION = "preference-v1"
PREFERENCE_NEUTRAL_PRIOR = 50.0


class LibraryFeedback(StrEnum):
    NEUTRAL = "neutral"
    LIKE = "like"
    DISLIKE = "dislike"


class AnalysisState(StrEnum):
    NOT_ANALYZED = "not_analyzed"
    AVAILABLE = "available"
    FAILED = "failed"


class ImplicitEventType(StrEnum):
    LIST_OPEN = "list_open"
    DETAIL_OPEN = "detail_open"
    PDF_OPEN = "pdf_open"
    READING_DURATION = "reading_duration"
    READING_COMPLETED = "reading_completed"
    ANALYSIS_REPORT_GENERATED = "analysis_report_generated"


class ReadingDurationBucket(StrEnum):
    UNDER_THIRTY_SECONDS = "lt_30s"
    THIRTY_SECONDS_TO_TWO_MINUTES = "30s_2m"
    TWO_TO_TEN_MINUTES = "2m_10m"
    OVER_TEN_MINUTES = "gte_10m"


@dataclass(frozen=True, slots=True)
class LibraryStateInput:
    favorite: bool
    feedback: LibraryFeedback
    feedback_reasons: tuple[str, ...]
    pinned: bool
    reading_progress: float
    analysis_state: AnalysisState

    def __post_init__(self) -> None:
        feedback = LibraryFeedback(self.feedback)
        analysis_state = AnalysisState(self.analysis_state)
        if not math.isfinite(self.reading_progress) or not 0 <= self.reading_progress <= 100:
            raise ValueError("reading_progress must be between 0 and 100")
        reasons: list[str] = []
        seen: set[str] = set()
        for raw_reason in self.feedback_reasons:
            reason = " ".join(raw_reason.split())
            if not reason or len(reason) > 160:
                raise ValueError("feedback reasons must contain 1-160 characters")
            identity = unicodedata.normalize("NFKC", reason).casefold()
            if identity not in seen:
                reasons.append(reason)
                seen.add(identity)
        if len(reasons) > 8:
            raise ValueError("at most eight feedback reasons are allowed")
        if feedback is LibraryFeedback.NEUTRAL and reasons:
            raise ValueError("neutral feedback cannot contain reasons")
        object.__setattr__(self, "feedback", feedback)
        object.__setattr__(self, "analysis_state", analysis_state)
        object.__setattr__(self, "feedback_reasons", tuple(reasons))
        object.__setattr__(self, "reading_progress", float(self.reading_progress))


@dataclass(frozen=True, slots=True)
class LibraryState:
    work_id: str
    favorite: bool
    feedback: LibraryFeedback
    feedback_reasons: tuple[str, ...]
    pinned: bool
    reading_progress: float
    analysis_state: AnalysisState
    persisted: bool
    created_at: datetime | None
    updated_at: datetime | None

    @property
    def historical_match(self) -> bool:
        return (
            self.favorite
            or self.reading_progress > 0
            or self.analysis_state is AnalysisState.AVAILABLE
        )


@dataclass(frozen=True, slots=True)
class InterestedAuthor:
    author_key: str
    display_name: str
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ImplicitEventInput:
    event_key: str
    event_type: ImplicitEventType
    occurred_at: datetime | None = None
    duration_bucket: ReadingDurationBucket | None = None

    def __post_init__(self) -> None:
        event_key = self.event_key.strip()
        if not event_key or len(event_key) > 128:
            raise ValueError("event_key must contain 1-128 characters")
        event_type = ImplicitEventType(self.event_type)
        bucket = (
            ReadingDurationBucket(self.duration_bucket)
            if self.duration_bucket is not None
            else None
        )
        if event_type is ImplicitEventType.READING_DURATION and bucket is None:
            raise ValueError("reading duration requires a duration bucket")
        if event_type is not ImplicitEventType.READING_DURATION and bucket is not None:
            raise ValueError("duration bucket is only valid for reading duration")
        if self.occurred_at is not None:
            _require_aware(self.occurred_at)
            object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        object.__setattr__(self, "event_key", event_key)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "duration_bucket", bucket)


@dataclass(frozen=True, slots=True)
class ImplicitPreferenceEvent:
    id: str
    work_id: str
    event_key: str
    event_type: ImplicitEventType
    duration_bucket: ReadingDurationBucket | None
    weight: float
    occurred_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PreferenceAggregationResult:
    cutoff: datetime
    event_count: int
    work_count: int


@dataclass(frozen=True, slots=True)
class PreferenceContextWorkEvidence:
    """Privacy-reduced facts permitted to influence keyword suggestions."""

    work_id: str
    title: str
    abstract: str | None
    sources: tuple[str, ...]
    aggregated_weight: float


@dataclass(frozen=True, slots=True)
class PreferenceScore:
    rule_version: str
    score: float
    coverage: float
    confidence: float
    suppressed_by_default: bool
    historical_match: bool
    matched_author_keys: tuple[str, ...]
    behavior_boost: float
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


_EVENT_WEIGHTS: dict[ImplicitEventType, float] = {
    ImplicitEventType.LIST_OPEN: 0.25,
    ImplicitEventType.DETAIL_OPEN: 0.5,
    ImplicitEventType.PDF_OPEN: 1.0,
    ImplicitEventType.READING_COMPLETED: 4.0,
    ImplicitEventType.ANALYSIS_REPORT_GENERATED: 3.0,
}
_DURATION_WEIGHTS: dict[ReadingDurationBucket, float] = {
    ReadingDurationBucket.UNDER_THIRTY_SECONDS: 0.25,
    ReadingDurationBucket.THIRTY_SECONDS_TO_TWO_MINUTES: 1.0,
    ReadingDurationBucket.TWO_TO_TEN_MINUTES: 2.0,
    ReadingDurationBucket.OVER_TEN_MINUTES: 3.0,
}


class PreferenceRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    def get_library_state(self, work_id: str) -> LibraryState:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            record = session.get(LibraryStateRecord, work.id)
            return self._library_domain(record, work.id)

    def set_library_state(self, work_id: str, value: LibraryStateInput) -> LibraryState:
        now = _require_aware(self._clock()).astimezone(UTC)
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            record = session.get(LibraryStateRecord, work.id)
            if record is None:
                record = LibraryStateRecord(
                    work_id=work.id,
                    favorite=value.favorite,
                    feedback=value.feedback.value,
                    feedback_reasons=list(value.feedback_reasons),
                    pinned=value.pinned,
                    reading_progress=value.reading_progress,
                    analysis_state=value.analysis_state.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.favorite = value.favorite
                record.feedback = value.feedback.value
                record.feedback_reasons = list(value.feedback_reasons)
                record.pinned = value.pinned
                record.reading_progress = value.reading_progress
                record.analysis_state = value.analysis_state.value
                record.updated_at = now
            session.commit()
            return self._library_domain(record, work.id)

    def record_analysis_report_generated(
        self,
        work_id: str,
        *,
        report_id: str,
    ) -> LibraryState:
        """Atomically mark availability and append the one permitted behavior fact."""

        if not report_id.strip() or len(report_id) > 80:
            raise ValueError("report_id must contain 1-80 characters")
        now = _require_aware(self._clock()).astimezone(UTC)
        event_key = f"analysis-report:{report_id}"
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            state = session.get(LibraryStateRecord, work.id)
            if state is None:
                state = LibraryStateRecord(
                    work_id=work.id,
                    favorite=False,
                    feedback=LibraryFeedback.NEUTRAL.value,
                    feedback_reasons=[],
                    pinned=False,
                    reading_progress=0.0,
                    analysis_state=AnalysisState.AVAILABLE.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(state)
            elif state.analysis_state != AnalysisState.AVAILABLE.value:
                state.analysis_state = AnalysisState.AVAILABLE.value
                state.updated_at = now
            event = session.scalar(
                select(ImplicitPreferenceEventRecord).where(
                    ImplicitPreferenceEventRecord.event_key == event_key
                )
            )
            if event is None:
                session.add(
                    ImplicitPreferenceEventRecord(
                        work_id=work.id,
                        event_key=event_key,
                        event_type=ImplicitEventType.ANALYSIS_REPORT_GENERATED.value,
                        duration_bucket=None,
                        weight=_EVENT_WEIGHTS[
                            ImplicitEventType.ANALYSIS_REPORT_GENERATED
                        ],
                        occurred_at=now,
                        created_at=now,
                    )
                )
            elif (
                event.work_id != work.id
                or event.event_type
                != ImplicitEventType.ANALYSIS_REPORT_GENERATED.value
            ):
                raise ValueError("analysis report event_key conflict")
            session.commit()
            return self._library_domain(state, work.id)

    def set_interested_author(
        self,
        *,
        author_key: str,
        display_name: str,
        active: bool,
    ) -> InterestedAuthor:
        key = _normalize_author_key(author_key)
        name = " ".join(display_name.split())
        if not name or len(name) > 255:
            raise ValueError("display_name must contain 1-255 characters")
        now = _require_aware(self._clock()).astimezone(UTC)
        with self._sessions() as session:
            record = session.get(InterestedAuthorRecord, key)
            if record is None:
                record = InterestedAuthorRecord(
                    author_key=key,
                    display_name=name,
                    active=active,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.display_name = name
                record.active = active
                record.updated_at = now
            session.commit()
            return self._author_domain(record)

    def list_interested_authors(
        self,
        *,
        include_inactive: bool = False,
    ) -> tuple[InterestedAuthor, ...]:
        with self._sessions() as session:
            query = select(InterestedAuthorRecord)
            if not include_inactive:
                query = query.where(InterestedAuthorRecord.active.is_(True))
            records = session.scalars(query.order_by(InterestedAuthorRecord.author_key))
            return tuple(self._author_domain(record) for record in records)

    def preference_context_works(
        self,
        *,
        limit: int = 500,
    ) -> tuple[PreferenceContextWorkEvidence, ...]:
        """Read explicit interests plus irreversible aggregates, never raw events."""

        if not 1 <= limit <= 2_000:
            raise ValueError("limit must be between 1 and 2000")
        with self._sessions() as session:
            selected: dict[str, set[str]] = {}
            rows = session.execute(
                select(LibraryStateRecord, WorkRecord)
                .join(WorkRecord, WorkRecord.id == LibraryStateRecord.work_id)
                .where(
                    WorkRecord.status == "active",
                    or_(
                        LibraryStateRecord.favorite.is_(True),
                        LibraryStateRecord.feedback == LibraryFeedback.LIKE.value,
                    ),
                )
                .order_by(WorkRecord.id)
                .limit(limit)
            )
            for state, work in rows:
                sources = selected.setdefault(work.id, set())
                if state.favorite:
                    sources.add("favorite")
                if state.feedback == LibraryFeedback.LIKE.value:
                    sources.add("liked")

            author_keys = tuple(
                session.scalars(
                    select(InterestedAuthorRecord.author_key)
                    .where(InterestedAuthorRecord.active.is_(True))
                    .order_by(InterestedAuthorRecord.author_key)
                    .limit(limit)
                )
            )
            if author_keys and len(selected) < limit:
                statement = text(
                    "SELECT DISTINCT works.id "
                    "FROM works JOIN json_each(works.authors) AS author "
                    "WHERE works.status = 'active' "
                    "AND lower(trim(author.value)) IN :author_keys "
                    "ORDER BY works.id LIMIT :limit"
                ).bindparams(bindparam("author_keys", expanding=True))
                candidate_ids = tuple(
                    str(value)
                    for value in session.scalars(
                        statement,
                        {
                            "author_keys": author_keys,
                            "limit": limit - len(selected),
                        },
                    )
                )
                if candidate_ids:
                    interested = set(author_keys)
                    candidates = session.scalars(
                        select(WorkRecord)
                        .where(
                            WorkRecord.id.in_(candidate_ids),
                            WorkRecord.status == "active",
                        )
                        .order_by(WorkRecord.id)
                    )
                    for work in candidates:
                        if any(
                            _normalize_author_key(author) in interested
                            for author in work.authors
                        ):
                            selected.setdefault(work.id, set()).add(
                                "interested_author"
                            )

            aggregate_ids = tuple(
                str(work_id)
                for work_id in session.scalars(
                    select(PreferenceAggregateRecord.work_id)
                    .join(WorkRecord, WorkRecord.id == PreferenceAggregateRecord.work_id)
                    .where(
                        WorkRecord.status == "active",
                        PreferenceAggregateRecord.total_weight > 0,
                    )
                    .order_by(
                        PreferenceAggregateRecord.total_weight.desc(),
                        PreferenceAggregateRecord.work_id,
                    )
                    .limit(limit)
                )
            )
            for work_id in aggregate_ids:
                if work_id not in selected and len(selected) >= limit:
                    break
                selected.setdefault(work_id, set()).add("aggregated_behavior")

            work_ids = tuple(sorted(selected))[:limit]
            if not work_ids:
                return ()
            works = {
                work.id: work
                for work in session.scalars(
                    select(WorkRecord)
                    .where(
                        WorkRecord.id.in_(work_ids),
                        WorkRecord.status == "active",
                    )
                    .order_by(WorkRecord.id)
                )
            }
            aggregates = {
                record.work_id: float(record.total_weight)
                for record in session.scalars(
                    select(PreferenceAggregateRecord).where(
                        PreferenceAggregateRecord.work_id.in_(work_ids)
                    )
                )
            }
            source_order = (
                "liked",
                "favorite",
                "interested_author",
                "aggregated_behavior",
            )
            return tuple(
                PreferenceContextWorkEvidence(
                    work_id=work.id,
                    title=work.canonical_title,
                    abstract=work.abstract,
                    sources=tuple(
                        source
                        for source in source_order
                        if source in selected[work.id]
                        or (
                            source == "aggregated_behavior"
                            and aggregates.get(work.id, 0.0) > 0
                        )
                    ),
                    aggregated_weight=aggregates.get(work.id, 0.0),
                )
                for work_id in work_ids
                if (work := works.get(work_id)) is not None
            )

    def record_implicit_event(
        self,
        work_id: str,
        value: ImplicitEventInput,
    ) -> ImplicitPreferenceEvent:
        occurred_at = value.occurred_at or self._clock()
        occurred_at = _require_aware(occurred_at).astimezone(UTC)
        weight = _event_weight(value.event_type, value.duration_bucket)
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            existing = session.scalar(
                select(ImplicitPreferenceEventRecord).where(
                    ImplicitPreferenceEventRecord.event_key == value.event_key
                )
            )
            if existing is not None:
                if (
                    existing.work_id != work.id
                    or existing.event_type != value.event_type.value
                    or existing.duration_bucket
                    != (value.duration_bucket.value if value.duration_bucket is not None else None)
                ):
                    raise ValueError("event_key conflict")
                return self._event_domain(existing)
            record = ImplicitPreferenceEventRecord(
                work_id=work.id,
                event_key=value.event_key,
                event_type=value.event_type.value,
                duration_bucket=(
                    value.duration_bucket.value if value.duration_bucket is not None else None
                ),
                weight=weight,
                occurred_at=occurred_at,
                created_at=_require_aware(self._clock()).astimezone(UTC),
            )
            session.add(record)
            session.commit()
            return self._event_domain(record)

    def list_raw_events(self, work_id: str) -> tuple[ImplicitPreferenceEvent, ...]:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            records = session.scalars(
                select(ImplicitPreferenceEventRecord)
                .where(ImplicitPreferenceEventRecord.work_id == work.id)
                .order_by(
                    ImplicitPreferenceEventRecord.occurred_at,
                    ImplicitPreferenceEventRecord.id,
                )
            )
            return tuple(self._event_domain(record) for record in records)

    def aggregate_expired_events(self, *, now: datetime) -> PreferenceAggregationResult:
        observed_now = _require_aware(now).astimezone(UTC)
        cutoff = observed_now - timedelta(days=RAW_EVENT_RETENTION_DAYS)
        with self._sessions() as session:
            events = list(
                session.scalars(
                    select(ImplicitPreferenceEventRecord)
                    .where(ImplicitPreferenceEventRecord.occurred_at < cutoff)
                    .order_by(
                        ImplicitPreferenceEventRecord.work_id,
                        ImplicitPreferenceEventRecord.occurred_at,
                        ImplicitPreferenceEventRecord.id,
                    )
                )
            )
            totals: dict[str, float] = {}
            for event in events:
                totals[event.work_id] = totals.get(event.work_id, 0.0) + event.weight
            for work_id, weight in totals.items():
                aggregate = session.get(PreferenceAggregateRecord, work_id)
                if aggregate is None:
                    aggregate = PreferenceAggregateRecord(
                        work_id=work_id,
                        total_weight=weight,
                        aggregated_through=cutoff,
                        updated_at=observed_now,
                    )
                    session.add(aggregate)
                else:
                    aggregate.total_weight += weight
                    aggregate.aggregated_through = max(
                        _utc(aggregate.aggregated_through),
                        cutoff,
                    )
                    aggregate.updated_at = observed_now
            for event in events:
                session.delete(event)
            session.commit()
        return PreferenceAggregationResult(
            cutoff=cutoff,
            event_count=len(events),
            work_count=len(totals),
        )

    def get_aggregated_weight(self, work_id: str) -> float:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            aggregate = session.get(PreferenceAggregateRecord, work.id)
            return float(aggregate.total_weight) if aggregate is not None else 0.0

    def get_total_implicit_weight(self, work_id: str) -> float:
        with self._sessions() as session:
            work = self._canonical_work(session, work_id)
            aggregate = session.get(PreferenceAggregateRecord, work.id)
            raw = session.scalar(
                select(func.coalesce(func.sum(ImplicitPreferenceEventRecord.weight), 0.0)).where(
                    ImplicitPreferenceEventRecord.work_id == work.id
                )
            )
            return float(raw or 0.0) + (
                float(aggregate.total_weight) if aggregate is not None else 0.0
            )

    def score_work(
        self,
        work_id: str,
        *,
        authors: Sequence[str] | None = None,
    ) -> PreferenceScore:
        if authors is None:
            with self._sessions() as session:
                authors = tuple(self._canonical_work(session, work_id).authors)
        state = self.get_library_state(work_id)
        interested_author_keys = tuple(
            author.author_key for author in self.list_interested_authors()
        )
        return score_preference(
            state,
            work_authors=authors,
            interested_author_keys=interested_author_keys,
            implicit_weight=self.get_total_implicit_weight(work_id),
        )

    def score_works(
        self,
        work_ids: Sequence[str],
    ) -> dict[str, PreferenceScore]:
        """Score a bounded candidate set from one consistent local-state read."""

        requested = tuple(dict.fromkeys(work_ids))
        if not requested:
            return {}
        with self._sessions() as session:
            canonical = {
                work_id: self._canonical_work(session, work_id)
                for work_id in requested
            }
            canonical_ids = tuple(dict.fromkeys(work.id for work in canonical.values()))
            states = {
                record.work_id: record
                for record in session.scalars(
                    select(LibraryStateRecord).where(
                        LibraryStateRecord.work_id.in_(canonical_ids)
                    )
                )
            }
            aggregates = {
                record.work_id: float(record.total_weight)
                for record in session.scalars(
                    select(PreferenceAggregateRecord).where(
                        PreferenceAggregateRecord.work_id.in_(canonical_ids)
                    )
                )
            }
            raw_weights = {
                str(work_id): float(weight or 0.0)
                for work_id, weight in session.execute(
                    select(
                        ImplicitPreferenceEventRecord.work_id,
                        func.coalesce(
                            func.sum(ImplicitPreferenceEventRecord.weight),
                            0.0,
                        ),
                    )
                    .where(ImplicitPreferenceEventRecord.work_id.in_(canonical_ids))
                    .group_by(ImplicitPreferenceEventRecord.work_id)
                )
            }
            interested_author_keys = tuple(
                session.scalars(
                    select(InterestedAuthorRecord.author_key)
                    .where(InterestedAuthorRecord.active.is_(True))
                    .order_by(InterestedAuthorRecord.author_key)
                )
            )

            return {
                requested_id: score_preference(
                    self._library_domain(states.get(work.id), work.id),
                    work_authors=tuple(work.authors),
                    interested_author_keys=interested_author_keys,
                    implicit_weight=(
                        aggregates.get(work.id, 0.0)
                        + raw_weights.get(work.id, 0.0)
                    ),
                )
                for requested_id, work in canonical.items()
            }

    @staticmethod
    def _canonical_work(session: Session, work_id: str) -> WorkRecord:
        work = session.get(WorkRecord, work_id)
        if work is None:
            raise KeyError(work_id)
        visited: set[str] = set()
        while work.status == "merged":
            if work.id in visited or work.merged_into_work_id is None:
                raise RuntimeError("Invalid Work merge chain")
            visited.add(work.id)
            target = session.get(WorkRecord, work.merged_into_work_id)
            if target is None:
                raise RuntimeError("Merged Work target is missing")
            work = target
        return work

    @staticmethod
    def _library_domain(record: LibraryStateRecord | None, work_id: str) -> LibraryState:
        if record is None:
            return LibraryState(
                work_id=work_id,
                favorite=False,
                feedback=LibraryFeedback.NEUTRAL,
                feedback_reasons=(),
                pinned=False,
                reading_progress=0.0,
                analysis_state=AnalysisState.NOT_ANALYZED,
                persisted=False,
                created_at=None,
                updated_at=None,
            )
        return LibraryState(
            work_id=record.work_id,
            favorite=record.favorite,
            feedback=LibraryFeedback(record.feedback),
            feedback_reasons=tuple(record.feedback_reasons),
            pinned=record.pinned,
            reading_progress=record.reading_progress,
            analysis_state=AnalysisState(record.analysis_state),
            persisted=True,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
        )

    @staticmethod
    def _author_domain(record: InterestedAuthorRecord) -> InterestedAuthor:
        return InterestedAuthor(
            author_key=record.author_key,
            display_name=record.display_name,
            active=record.active,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
        )

    @staticmethod
    def _event_domain(record: ImplicitPreferenceEventRecord) -> ImplicitPreferenceEvent:
        return ImplicitPreferenceEvent(
            id=record.id,
            work_id=record.work_id,
            event_key=record.event_key,
            event_type=ImplicitEventType(record.event_type),
            duration_bucket=(
                ReadingDurationBucket(record.duration_bucket)
                if record.duration_bucket is not None
                else None
            ),
            weight=record.weight,
            occurred_at=_utc(record.occurred_at),
            created_at=_utc(record.created_at),
        )


def _event_weight(
    event_type: ImplicitEventType,
    duration_bucket: ReadingDurationBucket | None,
) -> float:
    if event_type is ImplicitEventType.READING_DURATION:
        assert duration_bucket is not None
        return _DURATION_WEIGHTS[duration_bucket]
    return _EVENT_WEIGHTS[event_type]


def score_preference(
    state: LibraryState,
    *,
    work_authors: Sequence[str],
    interested_author_keys: Sequence[str],
    implicit_weight: float,
) -> PreferenceScore:
    """Compose bounded local preference evidence without changing paper quality."""

    if not math.isfinite(implicit_weight) or implicit_weight < 0:
        raise ValueError("implicit_weight must be finite and non-negative")
    interested = {_normalize_author_key(value) for value in interested_author_keys}
    matched_author_keys: list[str] = []
    seen_matches: set[str] = set()
    for author in work_authors:
        try:
            author_key = _normalize_author_key(author)
        except ValueError:
            continue
        if author_key in interested and author_key not in seen_matches:
            matched_author_keys.append(author_key)
            seen_matches.add(author_key)

    behavior_boost = min(float(implicit_weight), 10.0)
    suppressed = state.feedback is LibraryFeedback.DISLIKE
    score = PREFERENCE_NEUTRAL_PRIOR
    coverage = 0.0
    if state.feedback is not LibraryFeedback.NEUTRAL:
        coverage += 50.0
    if state.favorite:
        coverage += 20.0
    if matched_author_keys:
        coverage += 15.0
    if behavior_boost > 0:
        coverage += 15.0

    if suppressed:
        score = 0.0
    else:
        if state.feedback is LibraryFeedback.LIKE:
            score += 20.0
        if state.favorite:
            score += 10.0
        if matched_author_keys:
            score += 10.0
        score += behavior_boost
        score = min(score, 100.0)

    return PreferenceScore(
        rule_version=PREFERENCE_RULE_VERSION,
        score=round(score, 6),
        coverage=round(min(coverage, 100.0), 6),
        confidence=round(min(coverage, 100.0), 6),
        suppressed_by_default=suppressed,
        historical_match=state.historical_match,
        matched_author_keys=tuple(matched_author_keys),
        behavior_boost=round(behavior_boost, 6),
        evidence={
            "favorite": state.favorite,
            "feedback": state.feedback.value,
            "feedback_reasons": state.feedback_reasons,
            "matched_author_keys": tuple(matched_author_keys),
            "implicit_weight": float(implicit_weight),
            "behavior_boost_cap": 10.0,
        },
    )


def _normalize_author_key(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized or len(normalized) > 255:
        raise ValueError("author_key must contain 1-255 characters")
    return normalized


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
