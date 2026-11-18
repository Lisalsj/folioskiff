"""Agent-free reading briefing assembled from durable local product facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    DiscoveryPlanRecord,
    PlanRunRecord,
    RecommendationSetRecord,
)
from paper_reader.discovery.paper_updates import (
    PaperUpdateRepository,
    PaperUpdateState,
)
from paper_reader.discovery.plans import PlanScheduler
from paper_reader.jobs.models import JobRecord
from paper_reader.jobs.repository import JobRepository
from paper_reader.recommendation.result_api import (
    RecommendationResultReader,
    recommendation_paper_responses,
)
from paper_reader.recommendation.results import (
    RecommendationItem,
    RecommendationSetRepository,
)
from paper_reader.related_resources.local_hints import (
    LexicalSearch,
    LocalExplorationHint,
    LocalExplorationHintService,
)
from paper_reader.security import require_local_session

router = APIRouter(
    prefix="/api/v1",
    tags=["briefing"],
    dependencies=[Depends(require_local_session)],
)


class BriefingEmptyStateResponse(BaseModel):
    active: bool
    reason: str | None
    title: str | None
    description: str | None
    primary_action: str | None


class BriefingLatestRunResponse(BaseModel):
    id: str
    status: str
    months: tuple[str, ...]
    created_at: datetime
    completed_at: datetime | None
    recommendation_set_id: str | None
    recommendation_set_version: int | None
    recommendation_count: int


class BriefingPlanResponse(BaseModel):
    id: str
    name: str
    research_intent: str
    enabled: bool
    schedule_frequency: str
    sources: tuple[str, ...]
    latest_run: BriefingLatestRunResponse | None


class BriefingPaperResponse(BaseModel):
    work_id: str
    scored_work_id: str
    title: str
    authors: tuple[str, ...]
    plan_id: str
    run_id: str
    recommendation_set_id: str
    position: int | None
    channel: str | None
    composite_score: float
    relevance_score: float
    quality_score: float
    coverage: float
    confidence: float
    warnings: tuple[str, ...]


class BriefingRevisionResponse(BaseModel):
    id: str
    run_id: str | None
    state: str
    work_id: str
    title: str
    authors: tuple[str, ...]
    source: str
    changed_fields: tuple[str, ...]
    source_updated_at: datetime | None
    detected_at: datetime
    has_readable_document: bool


class BriefingCatchupResponse(BaseModel):
    id: str
    plan_id: str
    scheduled_for: datetime
    created_at: datetime


class BriefingJobResponse(BaseModel):
    id: str
    kind: str
    status: str
    progress: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ReadingBriefingResponse(BaseModel):
    generated_at: datetime
    empty_state: BriefingEmptyStateResponse
    plans: tuple[BriefingPlanResponse, ...]
    new_papers: tuple[BriefingPaperResponse, ...]
    important_revisions: tuple[BriefingRevisionResponse, ...]
    pending_catchups: tuple[BriefingCatchupResponse, ...]
    active_jobs: tuple[BriefingJobResponse, ...]
    failed_jobs: tuple[BriefingJobResponse, ...]
    exploration_hint: LocalExplorationHint | None = None


@dataclass(frozen=True, slots=True)
class _PaperEntry:
    plan_id: str
    run_id: str
    recommendation_set_id: str
    item: RecommendationItem


class BriefingService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        lexical_index: LexicalSearch | None = None,
    ) -> None:
        self._sessions = sessions
        self._results = RecommendationSetRepository(sessions)
        self._local_hints = LocalExplorationHintService(
            sessions,
            lexical_index=lexical_index,
        )

    def build(self, *, now: datetime | None = None) -> ReadingBriefingResponse:
        generated_at = self._utc(now or datetime.now(UTC))
        plans = self._plan_summaries()
        set_ids = [
            plan.latest_run.recommendation_set_id
            for plan in plans
            if plan.latest_run and plan.latest_run.recommendation_set_id
        ]
        selected = self._results.selected_for_sets(set_ids)
        paper_entries = [
            _PaperEntry(plan.id, plan.latest_run.id, plan.latest_run.recommendation_set_id, item)
            for plan in plans
            if plan.latest_run and plan.latest_run.recommendation_set_id
            for item in selected[plan.latest_run.recommendation_set_id]
        ]
        new_papers = self._new_papers(paper_entries)
        important_revisions = self._important_revisions()
        pending_catchups = tuple(
            BriefingCatchupResponse(
                id=item.id,
                plan_id=item.plan_id,
                scheduled_for=item.scheduled_for,
                created_at=item.created_at,
            )
            for item in PlanScheduler(self._sessions).list_pending(limit=20)
        )
        jobs = JobRepository(self._sessions)
        active_jobs = tuple(
            self._job_response(job)
            for job in jobs.list_jobs(limit=20, statuses=("queued", "running", "paused"))
        )
        failed_jobs = tuple(
            self._job_response(job) for job in jobs.list_jobs(limit=20, statuses=("failed",))
        )
        empty_state = self._empty_state(
            has_plans=bool(plans),
            has_activity=any(
                (
                    new_papers,
                    important_revisions,
                    pending_catchups,
                    active_jobs,
                    failed_jobs,
                )
            ),
        )
        exploration_hint = next(
            (
                hint
                for paper in new_papers[:5]
                if (hint := self._local_hints.for_work(paper.work_id)) is not None
            ),
            None,
        )
        return ReadingBriefingResponse(
            generated_at=generated_at,
            empty_state=empty_state,
            plans=plans,
            new_papers=new_papers,
            important_revisions=important_revisions,
            pending_catchups=pending_catchups,
            active_jobs=active_jobs,
            failed_jobs=failed_jobs,
            exploration_hint=exploration_hint,
        )

    def _plan_summaries(self) -> tuple[BriefingPlanResponse, ...]:
        latest_run = (
            select(PlanRunRecord.id)
            .where(PlanRunRecord.plan_id == DiscoveryPlanRecord.id)
            .order_by(PlanRunRecord.created_at.desc(), PlanRunRecord.id.desc())
            .limit(1)
            .correlate(DiscoveryPlanRecord)
            .scalar_subquery()
        )
        latest_set = (
            select(RecommendationSetRecord.id)
            .where(RecommendationSetRecord.run_id == PlanRunRecord.id)
            .order_by(RecommendationSetRecord.version.desc())
            .limit(1)
            .correlate(PlanRunRecord)
            .scalar_subquery()
        )
        query = (
            select(
                DiscoveryPlanRecord.id,
                DiscoveryPlanRecord.name,
                DiscoveryPlanRecord.research_intent,
                DiscoveryPlanRecord.enabled,
                DiscoveryPlanRecord.schedule_frequency,
                DiscoveryPlanRecord.sources,
                PlanRunRecord.id.label("run_id"),
                PlanRunRecord.status,
                PlanRunRecord.months,
                PlanRunRecord.created_at,
                PlanRunRecord.completed_at,
                RecommendationSetRecord.id.label("set_id"),
                RecommendationSetRecord.version,
                RecommendationSetRecord.recommendation_count,
            )
            .select_from(DiscoveryPlanRecord)
            .outerjoin(PlanRunRecord, PlanRunRecord.id == latest_run)
            .outerjoin(RecommendationSetRecord, RecommendationSetRecord.id == latest_set)
            .order_by(DiscoveryPlanRecord.created_at)
        )
        with self._sessions() as session:
            return tuple(
                BriefingPlanResponse(
                    id=row.id,
                    name=row.name,
                    research_intent=row.research_intent,
                    enabled=row.enabled,
                    schedule_frequency=row.schedule_frequency,
                    sources=tuple(row.sources),
                    latest_run=(
                        BriefingLatestRunResponse(
                            id=row.run_id,
                            status=row.status,
                            months=tuple(row.months),
                            created_at=self._utc(row.created_at),
                            completed_at=self._utc(row.completed_at) if row.completed_at else None,
                            recommendation_set_id=row.set_id,
                            recommendation_set_version=row.version,
                            recommendation_count=row.recommendation_count or 0,
                        )
                        if row.run_id else None
                    ),
                )
                for row in session.execute(query)
            )

    def _new_papers(
        self,
        entries: list[_PaperEntry],
    ) -> tuple[BriefingPaperResponse, ...]:
        if not entries:
            return ()
        items = tuple(entry.item for entry in entries)
        presentations = RecommendationResultReader(self._sessions).present(items)
        responses = recommendation_paper_responses(
            items,
            presentations=presentations,
        )
        paired = sorted(
            zip(entries, responses, strict=True),
            key=lambda pair: (
                -pair[1].scores.composite.score,
                pair[0].item.default_position is None,
                pair[0].item.default_position or 0,
                pair[1].work_id,
            ),
        )
        selected: list[BriefingPaperResponse] = []
        seen_work_ids: set[str] = set()
        for entry, paper in paired:
            if paper.work_id in seen_work_ids:
                continue
            seen_work_ids.add(paper.work_id)
            selected.append(
                BriefingPaperResponse(
                    work_id=paper.work_id,
                    scored_work_id=paper.scored_work_id,
                    title=paper.title,
                    authors=paper.authors,
                    plan_id=entry.plan_id,
                    run_id=entry.run_id,
                    recommendation_set_id=entry.recommendation_set_id,
                    position=entry.item.default_position,
                    channel=paper.channel,
                    composite_score=paper.scores.composite.score,
                    relevance_score=paper.scores.relevance.score,
                    quality_score=paper.scores.quality.score,
                    coverage=paper.scores.composite.coverage,
                    confidence=paper.scores.composite.confidence,
                    warnings=paper.warnings,
                )
            )
            if len(selected) == 20:
                break
        return tuple(selected)

    def _important_revisions(self) -> tuple[BriefingRevisionResponse, ...]:
        page = PaperUpdateRepository(self._sessions).list_visible(
            limit=20, offset=0, state=PaperUpdateState.UNREAD
        )
        return tuple(
            BriefingRevisionResponse(
                id=item.id,
                run_id=item.run_id,
                state=item.state.value,
                work_id=item.work.id,
                title=item.work.title,
                authors=item.work.authors,
                source=item.source.connector_id,
                changed_fields=item.changed_fields,
                source_updated_at=item.source_updated_at,
                detected_at=item.detected_at,
                has_readable_document=item.has_readable_document,
            )
            for item in page.items
        )

    @classmethod
    def _job_response(cls, job: JobRecord) -> BriefingJobResponse:
        return BriefingJobResponse(
            id=job.id,
            kind=job.kind,
            status=job.status,
            progress=job.progress,
            error_code=job.error_code,
            created_at=cls._utc(job.created_at),
            updated_at=cls._utc(job.updated_at),
        )

    @staticmethod
    def _empty_state(
        *,
        has_plans: bool,
        has_activity: bool,
    ) -> BriefingEmptyStateResponse:
        if not has_plans:
            return BriefingEmptyStateResponse(
                active=True,
                reason="no_discovery_plans",
                title="Create your first research watch",
                description=(
                    "Describe a research intent, confirm its keyword groups, "
                    "then choose months and official sources."
                ),
                primary_action="create_plan",
            )
        if not has_activity:
            return BriefingEmptyStateResponse(
                active=True,
                reason="no_recent_activity",
                title="Your reading briefing is quiet",
                description="Run a watch plan to collect and rank new papers.",
                primary_action="run_plan",
            )
        return BriefingEmptyStateResponse(
            active=False,
            reason=None,
            title=None,
            description=None,
            primary_action=None,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


@router.get("/briefing", response_model=ReadingBriefingResponse)
def reading_briefing(request: Request) -> ReadingBriefingResponse:
    return BriefingService(
        request.app.state.sessions,
        lexical_index=request.app.state.lexical_index,
    ).build()
