from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


def simple_definition():  # type: ignore[no-untyped-def]
    from paper_reader.discovery.keywords import ConceptGroup, KeywordSetDefinition

    return KeywordSetDefinition(
        required=(
            ConceptGroup(
                key="agent",
                label="智能体",
                terms=("AI agent", "人工智能智能体"),
            ),
        )
    )


@pytest.fixture
def plan_repositories(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.keywords import KeywordRepository
    from paper_reader.discovery.plans import DiscoveryPlanRepository
    from paper_reader.migrations import upgrade_database

    database = tmp_path / "plans.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    try:
        yield KeywordRepository(sessions), DiscoveryPlanRepository(sessions)
    finally:
        engine.dispose()


def test_fixed_month_range_is_inclusive_warns_over_24_and_uses_plan_timezone() -> None:
    from paper_reader.discovery.plans import DateRangeSpec

    date_range = DateRangeSpec(
        mode="fixed",
        start_month="2024-01",
        end_month="2026-02",
        timezone="Asia/Shanghai",
    )

    resolved = date_range.resolve(datetime(2026, 7, 13, tzinfo=UTC))

    assert resolved.months[0] == "2024-01"
    assert resolved.months[-1] == "2026-02"
    assert len(resolved.months) == 26
    assert resolved.warnings == ("date_range_over_24_months",)
    assert resolved.contains_updated_at(datetime(2023, 12, 31, 16, 30, tzinfo=UTC))
    assert not resolved.contains_updated_at(datetime(2023, 12, 31, 15, 59, tzinfo=UTC))


def test_rolling_range_includes_current_natural_month() -> None:
    from paper_reader.discovery.plans import DateRangeSpec

    date_range = DateRangeSpec(
        mode="rolling",
        rolling_months=3,
        timezone="Asia/Shanghai",
    )

    resolved = date_range.resolve(datetime(2026, 7, 13, 12, tzinfo=UTC))

    assert resolved.months == ("2026-05", "2026-06", "2026-07")
    assert resolved.warnings == ()


def test_plan_requires_a_confirmed_keyword_version(plan_repositories) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        UnconfirmedPlanKeywordsError,
    )

    keywords, plans = plan_repositories
    version = keywords.create_version(
        research_intent="agent research",
        definition=simple_definition(),
    )
    draft = DiscoveryPlanCreate(
        name="智能体月报",
        research_intent="agent research",
        keyword_version_id=version.id,
        date_range=DateRangeSpec(mode="rolling", rolling_months=1),
        sources=("arxiv", "openreview"),
    )

    with pytest.raises(UnconfirmedPlanKeywordsError, match="confirmed"):
        plans.create(draft)

    keywords.confirm(version.id)
    plan = plans.create(draft)

    assert plan.keyword_version_id == version.id
    assert plan.date_range.rolling_months == 1
    assert plan.sources == ("arxiv", "openreview")
    assert plan.quota.priority_limit == 20
    assert plan.quota.candidate_limit == 50


def test_daily_missed_occurrences_are_pending_idempotent_and_require_confirmation(
    plan_repositories,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        PlanScheduler,
        ScheduleSpec,
    )

    keywords, plans = plan_repositories
    version = keywords.create_version(
        research_intent="agent research",
        definition=simple_definition(),
    )
    keywords.confirm(version.id)
    plan = plans.create(
        DiscoveryPlanCreate(
            name="每天早读",
            research_intent="agent research",
            keyword_version_id=version.id,
            date_range=DateRangeSpec(mode="rolling", rolling_months=1),
            sources=("arxiv",),
            schedule=ScheduleSpec(frequency="daily", local_time="08:00"),
        ),
        now=datetime(2026, 7, 10, 1, tzinfo=UTC),  # 09:00 Asia/Shanghai
    )
    scheduler = PlanScheduler(plans.sessions)

    first_scan = scheduler.collect_missed(
        now=datetime(2026, 7, 13, 1, tzinfo=UTC),
    )
    second_scan = scheduler.collect_missed(
        now=datetime(2026, 7, 13, 1, tzinfo=UTC),
    )

    assert [item.plan_id for item in first_scan] == [plan.id, plan.id, plan.id]
    assert all(item.status == "pending" for item in first_scan)
    assert second_scan == []
    confirmed = scheduler.confirm(first_scan[0].id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at is not None


def test_plan_configuration_rejects_invalid_months_sources_and_schedule() -> None:
    from pydantic import ValidationError

    from paper_reader.discovery.plans import DateRangeSpec, DiscoveryPlanCreate, ScheduleSpec

    with pytest.raises(ValidationError):
        DateRangeSpec(mode="fixed", start_month="2026-13", end_month="2026-14")
    with pytest.raises(ValidationError, match="after"):
        DateRangeSpec(mode="fixed", start_month="2026-07", end_month="2026-06")
    with pytest.raises(ValidationError):
        ScheduleSpec(frequency="monthly", monthly_day=31)
    with pytest.raises(ValidationError, match="unique"):
        DiscoveryPlanCreate(
            name="重复来源",
            research_intent="agents",
            keyword_version_id="version",
            sources=("arxiv", "arxiv"),
        )


def test_plan_domain_rejects_every_ambiguous_boundary_and_missing_record(
    plan_repositories,
) -> None:  # type: ignore[no-untyped-def]
    from pydantic import ValidationError

    from paper_reader.discovery.plans import (
        DateRangeSpec,
        DiscoveryPlanCreate,
        PlanQuota,
        PlanScheduler,
        ScheduleSpec,
        _require_aware,
    )

    keywords, plans = plan_repositories
    with pytest.raises(ValueError, match="timezone-aware"):
        _require_aware(datetime(2026, 7, 13))
    with pytest.raises(ValidationError, match="YYYY-MM"):
        DateRangeSpec(mode="fixed", start_month="2026/07", end_month="2026-08")
    with pytest.raises(ValidationError, match="requires"):
        DateRangeSpec(mode="fixed")
    with pytest.raises(ValidationError, match="cannot include"):
        DateRangeSpec(mode="rolling", start_month="2026-07")
    with pytest.raises(ValidationError, match="valid 24-hour"):
        ScheduleSpec(local_time="25:00")
    with pytest.raises(ValidationError, match="lower"):
        PlanQuota(priority_limit=20, candidate_limit=10)
    with pytest.raises(ValidationError, match="invalid format"):
        DiscoveryPlanCreate(
            name="非法来源",
            research_intent="agents",
            keyword_version_id="version",
            sources=("A",),
        )
    with pytest.raises(ValidationError, match="belong"):
        DiscoveryPlanCreate(
            name="越界过滤器",
            research_intent="agents",
            keyword_version_id="version",
            sources=("arxiv",),
            source_filters={"ieee": {"open_access": True}},
        )
    with pytest.raises(KeyError):
        plans.get("missing-plan")

    scheduler = PlanScheduler(plans.sessions)
    with pytest.raises(KeyError):
        scheduler.get("missing-catchup")
    with pytest.raises(KeyError):
        scheduler.confirm("missing-catchup")

    version = keywords.create_version(
        research_intent="agent research",
        definition=simple_definition(),
    )
    keywords.confirm(version.id)
    plans.create(
        DiscoveryPlanCreate(
            name="每周一早读",
            research_intent="agent research",
            keyword_version_id=version.id,
            sources=("arxiv",),
            schedule=ScheduleSpec(frequency="weekly", weekly_day=0, local_time="08:00"),
        ),
        now=datetime(2026, 7, 12, 1, tzinfo=UTC),
    )
    catchups = scheduler.collect_missed(now=datetime(2026, 7, 14, 1, tzinfo=UTC))
    assert len(catchups) == 1
    first = scheduler.confirm(catchups[0].id)
    repeated = scheduler.confirm(catchups[0].id)
    assert repeated.confirmed_at == first.confirmed_at
