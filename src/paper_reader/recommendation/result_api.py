"""Explainable recommendation result reads and explicit manual rescoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from itertools import batched
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    LibraryStateRecord,
    SourceRecord,
    WorkRecord,
)
from paper_reader.discovery.runs import DiscoveryRunRepository
from paper_reader.recommendation.orchestration import RecommendationOrchestrator
from paper_reader.recommendation.ranking import RankingView
from paper_reader.recommendation.results import (
    RecommendationItem,
    RecommendationSetRepository,
)
from paper_reader.recommendation.workers import resume_recommendation
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1",
    tags=["recommendations"],
    dependencies=[Depends(require_local_session)],
)

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", max_length=120),
]


class ScoreSummaryResponse(BaseModel):
    snapshot_id: str
    rule_version: str
    score: float
    coverage: float
    confidence: float


class ScoreBundleResponse(BaseModel):
    relevance: ScoreSummaryResponse
    quality: ScoreSummaryResponse
    freshness: ScoreSummaryResponse
    preference: ScoreSummaryResponse
    composite: ScoreSummaryResponse


class LibraryStateSummaryResponse(BaseModel):
    work_id: str
    favorite: bool
    feedback: str
    feedback_reasons: tuple[str, ...]
    pinned: bool
    reading_progress: float
    analysis_state: str
    persisted: bool
    created_at: datetime | None
    updated_at: datetime | None


class EvidenceSummaryResponse(BaseModel):
    keyword_eligible: bool
    preference_suppressed: bool
    eligible_by_default: bool
    historical_match: bool
    ineligibility_reasons: tuple[str, ...]
    exploration_reasons: tuple[str, ...]
    integrity_status: str
    integrity_coverage: float
    integrity_warning_codes: tuple[str, ...]


class RecommendationPaperResponse(BaseModel):
    item_id: str
    work_id: str
    scored_work_id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None
    sources: tuple[str, ...]
    source_updated_at: datetime | None
    default_position: int | None
    selected: bool
    historical_match: bool
    eligible_by_default: bool
    channel: str | None
    scores: ScoreBundleResponse
    evidence_summary: EvidenceSummaryResponse
    warnings: tuple[str, ...]
    library_state: LibraryStateSummaryResponse


class RecommendationRunResponse(BaseModel):
    run_id: str
    keyword_version_id: str
    recommendation_set_id: str
    recommendation_set_version: int
    set_sha256: str
    view: RankingView
    available_views: tuple[RankingView, ...]
    fixed_historical_months: bool
    requested_limit: int
    candidate_cap: int
    candidate_count: int
    recommendation_count: int
    exploration_target: int
    exploration_count: int
    degradation_reasons: tuple[str, ...]
    created_at: datetime
    items: tuple[RecommendationPaperResponse, ...]
    historical_matches: tuple[RecommendationPaperResponse, ...]


class RecommendationRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptedRecommendationJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class WorkPresentation:
    work_id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None
    sources: tuple[str, ...]
    library_state: LibraryStateSummaryResponse


class RecommendationResultReader:
    """Resolve current Work presentation without rewriting frozen score identity."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def present(
        self,
        items: Sequence[RecommendationItem],
    ) -> dict[str, WorkPresentation]:
        scored_ids = dict.fromkeys(item.scored_work_id for item in items)
        presentations: dict[str, WorkPresentation] = {}
        with self._sessions() as session:
            for batch in batched(scored_ids, 500):
                works = self._canonical_works(session, batch)
                canonical_ids = {work.id for work in works.values()}
                states = {
                    record.work_id: record
                    for record in session.scalars(
                        select(LibraryStateRecord).where(
                            LibraryStateRecord.work_id.in_(canonical_ids)
                        )
                    )
                }
                sources: dict[str, set[str]] = {work_id: set() for work_id in canonical_ids}
                for work_id, connector_id in session.execute(
                    select(SourceRecord.work_id, SourceRecord.connector_id).where(
                        SourceRecord.work_id.in_(canonical_ids)
                    )
                ):
                    sources[work_id].add(connector_id)
                for scored_id, work in works.items():
                    presentations[scored_id] = WorkPresentation(
                        work_id=work.id,
                        title=work.canonical_title,
                        authors=tuple(work.authors),
                        doi=work.doi,
                        sources=tuple(sorted(sources[work.id])),
                        library_state=self._library_state(states.get(work.id), work.id),
                    )
        return presentations

    @staticmethod
    def _canonical_works(session: Session, scored_ids: tuple[str, ...]) -> dict[str, WorkRecord]:
        records: dict[str, WorkRecord] = {}
        pending = set(scored_ids)
        while pending:
            loaded = session.scalars(select(WorkRecord).where(WorkRecord.id.in_(pending))).all()
            records.update((work.id, work) for work in loaded)
            pending = {
                work.merged_into_work_id
                for work in loaded
                if work.status == "merged" and work.merged_into_work_id is not None
            }.difference(records)

        canonical: dict[str, WorkRecord] = {}
        for work_id in scored_ids:
            work = records[work_id]
            visited: set[str] = set()
            while work.status == "merged":
                if work.id in visited or work.merged_into_work_id is None:
                    raise RuntimeError("Invalid Work merge chain")
                visited.add(work.id)
                target = records.get(work.merged_into_work_id)
                if target is None:
                    raise RuntimeError("Merged Work target is missing")
                work = target
            canonical[work_id] = work
        return canonical

    @classmethod
    def _library_state(
        cls,
        record: LibraryStateRecord | None,
        work_id: str,
    ) -> LibraryStateSummaryResponse:
        if record is None:
            return LibraryStateSummaryResponse(
                work_id=work_id,
                favorite=False,
                feedback="neutral",
                feedback_reasons=(),
                pinned=False,
                reading_progress=0,
                analysis_state="not_analyzed",
                persisted=False,
                created_at=None,
                updated_at=None,
            )
        return LibraryStateSummaryResponse(
            work_id=record.work_id,
            favorite=record.favorite,
            feedback=record.feedback,
            feedback_reasons=tuple(record.feedback_reasons),
            pinned=record.pinned,
            reading_progress=record.reading_progress,
            analysis_state=record.analysis_state,
            persisted=True,
            created_at=cls._utc(record.created_at),
            updated_at=cls._utc(record.updated_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def recommendation_paper_responses(
    items: Sequence[RecommendationItem],
    *,
    presentations: Mapping[str, WorkPresentation],
) -> tuple[RecommendationPaperResponse, ...]:
    return tuple(
        _recommendation_paper_response(
            item,
            presentation=presentations[item.scored_work_id],
        )
        for item in items
    )


def _recommendation_paper_response(
    item: RecommendationItem,
    *,
    presentation: WorkPresentation,
) -> RecommendationPaperResponse:
    raw_signals = item.ranking_evidence.get("signals")
    if not isinstance(raw_signals, dict):
        raise RuntimeError("Recommendation item has invalid score evidence")
    scores = ScoreBundleResponse(
        **{
            name: _score_summary(raw_signals, name)
            for name in (
                "relevance",
                "quality",
                "freshness",
                "preference",
                "composite",
            )
        }
    )
    raw_constraints = item.ranking_evidence.get("hard_constraints")
    constraints = raw_constraints if isinstance(raw_constraints, dict) else {}
    raw_integrity = constraints.get("integrity")
    integrity = raw_integrity if isinstance(raw_integrity, dict) else {}
    raw_warnings = integrity.get("warnings")
    integrity_warnings = raw_warnings if isinstance(raw_warnings, list) else []
    warning_codes = tuple(
        str(warning["code"])
        for warning in integrity_warnings
        if isinstance(warning, dict) and isinstance(warning.get("code"), str)
    )
    warnings = tuple(dict.fromkeys((*item.ineligibility_reasons, *warning_codes)))
    integrity_coverage = integrity.get("evidence_coverage", 0)
    return RecommendationPaperResponse(
        item_id=item.id,
        work_id=presentation.work_id,
        scored_work_id=item.scored_work_id,
        title=presentation.title,
        authors=presentation.authors,
        doi=presentation.doi,
        sources=presentation.sources,
        source_updated_at=item.source_updated_at,
        default_position=item.default_position,
        selected=item.selected,
        historical_match=item.historical_match,
        eligible_by_default=item.eligible_by_default,
        channel=item.channel.value if item.channel is not None else None,
        scores=scores,
        evidence_summary=EvidenceSummaryResponse(
            keyword_eligible=bool(item.ranking_evidence.get("keyword_eligible")),
            preference_suppressed=bool(
                item.ranking_evidence.get("preference_suppressed")
            ),
            eligible_by_default=item.eligible_by_default,
            historical_match=item.historical_match,
            ineligibility_reasons=item.ineligibility_reasons,
            exploration_reasons=tuple(reason.value for reason in item.exploration_reasons),
            integrity_status=str(integrity.get("status", "unknown")),
            integrity_coverage=(
                float(integrity_coverage)
                if isinstance(integrity_coverage, int | float)
                and not isinstance(integrity_coverage, bool)
                else 0.0
            ),
            integrity_warning_codes=warning_codes,
        ),
        warnings=warnings,
        library_state=presentation.library_state,
    )


def _score_summary(
    signals: Mapping[str, Any],
    name: str,
) -> ScoreSummaryResponse:
    raw = signals.get(name)
    if not isinstance(raw, dict):
        raise RuntimeError(f"Recommendation item has no {name} score evidence")
    try:
        return ScoreSummaryResponse(
            snapshot_id=str(raw["snapshot_id"]),
            rule_version=str(raw["rule_version"]),
            score=float(raw["score"]),
            coverage=float(raw["coverage"]),
            confidence=float(raw["confidence"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Recommendation item has invalid {name} evidence") from error


@router.get(
    "/runs/{run_id}/recommendations",
    response_model=RecommendationRunResponse,
)
def recommendation_results(
    run_id: str,
    request: Request,
    view: Annotated[RankingView, Query()] = RankingView.COMPOSITE,
    include_ineligible: Annotated[bool, Query()] = False,
) -> RecommendationRunResponse:
    runs = DiscoveryRunRepository(request.app.state.sessions)
    try:
        runs.get(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discovery run not found",
        ) from error
    repository = RecommendationSetRepository(request.app.state.sessions)
    result = repository.latest_for_run(run_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recommendation result not found",
        )
    selected = repository.view(
        result.id,
        view=view,
        include_ineligible=include_ineligible,
    )
    all_items = (*selected.items, *selected.historical_matches)
    presentations = RecommendationResultReader(request.app.state.sessions).present(
        all_items
    )
    return RecommendationRunResponse(
        run_id=result.run_id,
        keyword_version_id=result.keyword_version_id,
        recommendation_set_id=result.id,
        recommendation_set_version=result.version,
        set_sha256=result.set_sha256,
        view=view,
        available_views=tuple(RankingView),
        fixed_historical_months=result.fixed_historical_months,
        requested_limit=result.requested_limit,
        candidate_cap=result.candidate_cap,
        candidate_count=result.candidate_count,
        recommendation_count=result.recommendation_count,
        exploration_target=result.exploration_target,
        exploration_count=result.exploration_count,
        degradation_reasons=result.degradation_reasons,
        created_at=result.created_at,
        items=recommendation_paper_responses(
            selected.items,
            presentations=presentations,
        ),
        historical_matches=recommendation_paper_responses(
            selected.historical_matches,
            presentations=presentations,
        ),
    )


@router.post(
    "/runs/{run_id}/recommendations/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedRecommendationJob,
    dependencies=[Depends(require_local_mutation)],
)
async def refresh_recommendations(
    run_id: str,
    request: Request,
    payload: RecommendationRefreshRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> AcceptedRecommendationJob:
    del payload
    orchestrator: RecommendationOrchestrator | None = (
        request.app.state.recommendation_orchestrator
    )
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendation service is not ready",
        )
    try:
        job = orchestrator.create_job(
            run_id,
            trigger="manual",
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discovery run not found",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"recommendation:{job.id}",
            partial(resume_recommendation, orchestrator, job.id),
        )
    return AcceptedRecommendationJob.model_validate(job)
