"""Month-scoped discovery plans and user-confirmed missed schedule occurrences."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.keywords import KeywordRepository
from paper_reader.discovery.models import DiscoveryPlanRecord, PlanCatchupRecord

MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
SOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,79}$")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_month(value: str) -> tuple[int, int]:
    match = MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("month must use YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError("month must be between 01 and 12")
    return year, month


def _month_index(value: str) -> int:
    year, month = _parse_month(value)
    return (year * 12) + month - 1


def _month_from_index(value: int) -> str:
    year, zero_based_month = divmod(value, 12)
    return f"{year:04d}-{zero_based_month + 1:02d}"


@dataclass(frozen=True, slots=True)
class ResolvedDateRange:
    timezone: str
    months: tuple[str, ...]
    warnings: tuple[str, ...]

    def contains_updated_at(self, updated_at: datetime) -> bool:
        aware = _require_aware(updated_at)
        local = aware.astimezone(ZoneInfo(self.timezone))
        return f"{local.year:04d}-{local.month:02d}" in self.months


class DateRangeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["fixed", "rolling"] = "rolling"
    start_month: str | None = None
    end_month: str | None = None
    rolling_months: int | None = Field(default=None, ge=1)
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown timezone") from error
        if self.mode == "fixed":
            if self.start_month is None or self.end_month is None:
                raise ValueError("fixed range requires start_month and end_month")
            if _month_index(self.start_month) > _month_index(self.end_month):
                raise ValueError("start_month cannot be after end_month")
            object.__setattr__(self, "rolling_months", None)
        else:
            if self.start_month is not None or self.end_month is not None:
                raise ValueError("rolling range cannot include fixed months")
            object.__setattr__(self, "rolling_months", self.rolling_months or 1)
        return self

    def resolve(self, now: datetime) -> ResolvedDateRange:
        local_now = _require_aware(now).astimezone(ZoneInfo(self.timezone))
        if self.mode == "fixed":
            if self.start_month is None or self.end_month is None:  # pragma: no cover
                raise RuntimeError("validated fixed range is incomplete")
            first = _month_index(self.start_month)
            last = _month_index(self.end_month)
        else:
            count = self.rolling_months or 1
            last = (local_now.year * 12) + local_now.month - 1
            first = last - count + 1
        months = tuple(_month_from_index(item) for item in range(first, last + 1))
        warnings = ("date_range_over_24_months",) if len(months) > 24 else ()
        return ResolvedDateRange(self.timezone, months, warnings)


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    frequency: Literal["manual", "daily", "weekly", "monthly"] = "manual"
    local_time: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}$")
    weekly_day: int = Field(default=0, ge=0, le=6)
    monthly_day: int = Field(default=1, ge=1, le=28)

    @field_validator("local_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        hour, minute = (int(part) for part in value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("local_time must be a valid 24-hour time")
        return value


class PlanQuota(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority_limit: int = Field(default=20, ge=1)
    candidate_limit: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def candidates_cover_priority(self) -> Self:
        if self.candidate_limit < self.priority_limit:
            raise ValueError("candidate_limit cannot be lower than priority_limit")
        return self


class DiscoveryPlanCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=160)
    research_intent: str = Field(min_length=1, max_length=10_000)
    keyword_version_id: str = Field(min_length=1, max_length=36)
    date_range: DateRangeSpec = Field(default_factory=DateRangeSpec)
    sources: tuple[str, ...] = Field(min_length=1)
    source_filters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    schedule: ScheduleSpec = Field(default_factory=ScheduleSpec)
    quota: PlanQuota = Field(default_factory=PlanQuota)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("source IDs must be unique")
        if any(SOURCE_PATTERN.fullmatch(source) is None for source in self.sources):
            raise ValueError("source ID has an invalid format")
        unknown_filters = set(self.source_filters) - set(self.sources)
        if unknown_filters:
            raise ValueError("source filters must belong to selected sources")
        return self


def freeze_default_timezone(
    payload: DiscoveryPlanCreate,
    *,
    resolved_timezone: str,
) -> DiscoveryPlanCreate:
    """Freeze the current preference only when an API client omitted timezone."""

    if "timezone" in payload.date_range.model_fields_set:
        return payload
    date_range = payload.date_range.model_copy(update={"timezone": resolved_timezone})
    return payload.model_copy(update={"date_range": date_range})


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    id: str
    name: str
    research_intent: str
    keyword_version_id: str
    date_range: DateRangeSpec
    sources: tuple[str, ...]
    source_filters: dict[str, dict[str, Any]]
    schedule: ScheduleSpec
    quota: PlanQuota
    enabled: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlanCatchup:
    id: str
    plan_id: str
    scheduled_for: datetime
    status: str
    created_at: datetime
    confirmed_at: datetime | None


class UnconfirmedPlanKeywordsError(ValueError):
    pass


class DiscoveryPlanRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create(
        self,
        payload: DiscoveryPlanCreate,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPlan:
        keyword_version = KeywordRepository(self.sessions).get(payload.keyword_version_id)
        if keyword_version.confirmed_at is None:
            raise UnconfirmedPlanKeywordsError(
                "Discovery plans require a confirmed keyword version"
            )
        created_at = _require_aware(now or datetime.now(UTC)).astimezone(UTC)
        record = DiscoveryPlanRecord(
            name=" ".join(payload.name.split()),
            research_intent=" ".join(payload.research_intent.split()),
            keyword_version_id=payload.keyword_version_id,
            date_mode=payload.date_range.mode,
            start_month=payload.date_range.start_month,
            end_month=payload.date_range.end_month,
            rolling_months=payload.date_range.rolling_months,
            timezone=payload.date_range.timezone,
            sources=list(payload.sources),
            source_filters=payload.source_filters,
            schedule_frequency=payload.schedule.frequency,
            schedule_local_time=payload.schedule.local_time,
            weekly_day=payload.schedule.weekly_day,
            monthly_day=payload.schedule.monthly_day,
            priority_limit=payload.quota.priority_limit,
            candidate_limit=payload.quota.candidate_limit,
            enabled=payload.enabled,
            schedule_checked_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        with self.sessions() as session:
            session.add(record)
            session.commit()
            return self._to_domain(record)

    def list(self) -> list[DiscoveryPlan]:
        with self.sessions() as session:
            records = session.scalars(
                select(DiscoveryPlanRecord).order_by(DiscoveryPlanRecord.created_at)
            )
            return [self._to_domain(record) for record in records]

    def get(self, plan_id: str) -> DiscoveryPlan:
        with self.sessions() as session:
            record = session.get(DiscoveryPlanRecord, plan_id)
            if record is None:
                raise KeyError(plan_id)
            return self._to_domain(record)

    @staticmethod
    def _to_domain(record: DiscoveryPlanRecord) -> DiscoveryPlan:
        date_range = DateRangeSpec.model_validate(
            {
                "mode": record.date_mode,
                "start_month": record.start_month,
                "end_month": record.end_month,
                "rolling_months": record.rolling_months,
                "timezone": record.timezone,
            }
        )
        return DiscoveryPlan(
            id=record.id,
            name=record.name,
            research_intent=record.research_intent,
            keyword_version_id=record.keyword_version_id,
            date_range=date_range,
            sources=tuple(record.sources),
            source_filters=dict(record.source_filters),
            schedule=ScheduleSpec.model_validate(
                {
                    "frequency": record.schedule_frequency,
                    "local_time": record.schedule_local_time,
                    "weekly_day": record.weekly_day,
                    "monthly_day": record.monthly_day,
                }
            ),
            quota=PlanQuota(
                priority_limit=record.priority_limit,
                candidate_limit=record.candidate_limit,
            ),
            enabled=record.enabled,
            created_at=_stored_utc(record.created_at),
        )


class PlanScheduler:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def collect_missed(self, *, now: datetime | None = None) -> list[PlanCatchup]:
        checked_at = _require_aware(now or datetime.now(UTC)).astimezone(UTC)
        created: list[PlanCatchupRecord] = []
        with self._sessions() as session:
            plans = session.scalars(
                select(DiscoveryPlanRecord).where(
                    DiscoveryPlanRecord.enabled.is_(True),
                    DiscoveryPlanRecord.schedule_frequency != "manual",
                )
            )
            for plan in plans:
                cursor = _stored_utc(plan.schedule_checked_at)
                for occurrence in self._occurrences(plan, cursor=cursor, now=checked_at):
                    record = PlanCatchupRecord(
                        plan_id=plan.id,
                        scheduled_for=occurrence,
                        created_at=checked_at,
                    )
                    session.add(record)
                    created.append(record)
                plan.schedule_checked_at = checked_at
            session.commit()
            return [
                self._to_catchup(item)
                for item in sorted(created, key=lambda value: value.scheduled_for)
            ]

    def list_pending(self, *, limit: int | None = None) -> list[PlanCatchup]:
        with self._sessions() as session:
            records = session.scalars(
                select(PlanCatchupRecord)
                .where(PlanCatchupRecord.status == "pending")
                .order_by(PlanCatchupRecord.scheduled_for)
                .limit(limit)
            )
            return [self._to_catchup(record) for record in records]

    def get(self, catchup_id: str) -> PlanCatchup:
        with self._sessions() as session:
            record = session.get(PlanCatchupRecord, catchup_id)
            if record is None:
                raise KeyError(catchup_id)
            return self._to_catchup(record)

    def confirm(self, catchup_id: str, *, now: datetime | None = None) -> PlanCatchup:
        confirmed_at = _require_aware(now or datetime.now(UTC)).astimezone(UTC)
        with self._sessions() as session:
            record = session.get(PlanCatchupRecord, catchup_id)
            if record is None:
                raise KeyError(catchup_id)
            if record.status == "pending":
                record.status = "confirmed"
                record.confirmed_at = confirmed_at
                session.commit()
            return self._to_catchup(record)

    @staticmethod
    def _occurrences(
        plan: DiscoveryPlanRecord,
        *,
        cursor: datetime,
        now: datetime,
    ) -> list[datetime]:
        zone = ZoneInfo(plan.timezone)
        local_cursor = cursor.astimezone(zone)
        local_now = now.astimezone(zone)
        hour, minute = (int(part) for part in plan.schedule_local_time.split(":"))
        current = local_cursor.date()
        final = local_now.date()
        occurrences: list[datetime] = []
        while current <= final:
            matches = (
                plan.schedule_frequency == "daily"
                or (plan.schedule_frequency == "weekly" and current.weekday() == plan.weekly_day)
                or (plan.schedule_frequency == "monthly" and current.day == plan.monthly_day)
            )
            if matches:
                candidate = datetime.combine(current, time(hour, minute), tzinfo=zone).astimezone(
                    UTC
                )
                if cursor < candidate <= now:
                    occurrences.append(candidate)
            current += timedelta(days=1)
        return occurrences

    @staticmethod
    def _to_catchup(record: PlanCatchupRecord) -> PlanCatchup:
        return PlanCatchup(
            id=record.id,
            plan_id=record.plan_id,
            scheduled_for=_stored_utc(record.scheduled_for),
            status=record.status,
            created_at=_stored_utc(record.created_at),
            confirmed_at=(
                _stored_utc(record.confirmed_at) if record.confirmed_at is not None else None
            ),
        )
