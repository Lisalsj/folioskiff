"""HTTP API for month-scoped discovery plans and missed-run confirmation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from paper_reader.discovery.keywords import KeywordVersionNotFoundError
from paper_reader.discovery.plans import (
    DateRangeSpec,
    DiscoveryPlan,
    DiscoveryPlanCreate,
    DiscoveryPlanRepository,
    PlanCatchup,
    PlanQuota,
    PlanScheduler,
    ScheduleSpec,
    UnconfirmedPlanKeywordsError,
    freeze_default_timezone,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/plans",
    tags=["plans"],
    dependencies=[Depends(require_local_session)],
)


class DiscoveryPlanResponse(BaseModel):
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
    warnings: tuple[str, ...]

    @classmethod
    def from_domain(cls, plan: DiscoveryPlan) -> DiscoveryPlanResponse:
        return cls(
            id=plan.id,
            name=plan.name,
            research_intent=plan.research_intent,
            keyword_version_id=plan.keyword_version_id,
            date_range=plan.date_range,
            sources=plan.sources,
            source_filters=plan.source_filters,
            schedule=plan.schedule,
            quota=plan.quota,
            enabled=plan.enabled,
            created_at=plan.created_at,
            warnings=plan.date_range.resolve(datetime.now(UTC)).warnings,
        )


class PlanCatchupResponse(BaseModel):
    id: str
    plan_id: str
    scheduled_for: datetime
    status: str
    created_at: datetime
    confirmed_at: datetime | None

    @classmethod
    def from_domain(cls, catchup: PlanCatchup) -> PlanCatchupResponse:
        return cls(
            id=catchup.id,
            plan_id=catchup.plan_id,
            scheduled_for=catchup.scheduled_for,
            status=catchup.status,
            created_at=catchup.created_at,
            confirmed_at=catchup.confirmed_at,
        )


def plan_repository(request: Request) -> DiscoveryPlanRepository:
    return DiscoveryPlanRepository(request.app.state.sessions)


PlanRepo = Annotated[DiscoveryPlanRepository, Depends(plan_repository)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DiscoveryPlanResponse,
    dependencies=[Depends(require_local_mutation)],
)
def create_plan(
    payload: DiscoveryPlanCreate,
    request: Request,
    repo: PlanRepo,
) -> DiscoveryPlanResponse:
    payload = freeze_default_timezone(
        payload,
        resolved_timezone=request.app.state.user_preferences_service.resolved_timezone(),
    )
    try:
        return DiscoveryPlanResponse.from_domain(repo.create(payload))
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword version not found",
        ) from error
    except UnconfirmedPlanKeywordsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("", response_model=list[DiscoveryPlanResponse])
def list_plans(repo: PlanRepo) -> list[DiscoveryPlanResponse]:
    return [DiscoveryPlanResponse.from_domain(plan) for plan in repo.list()]


@router.get("/catchups", response_model=list[PlanCatchupResponse])
def list_catchups(request: Request) -> list[PlanCatchupResponse]:
    scheduler = PlanScheduler(request.app.state.sessions)
    return [PlanCatchupResponse.from_domain(item) for item in scheduler.list_pending()]


@router.post(
    "/catchups/scan",
    response_model=list[PlanCatchupResponse],
    dependencies=[Depends(require_local_mutation)],
)
def scan_catchups(request: Request) -> list[PlanCatchupResponse]:
    scheduler = PlanScheduler(request.app.state.sessions)
    return [PlanCatchupResponse.from_domain(item) for item in scheduler.collect_missed()]
