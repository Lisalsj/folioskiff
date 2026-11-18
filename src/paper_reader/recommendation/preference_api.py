"""Local library state, preference events, and explainable preference scores."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.recommendation.preferences import (
    AnalysisState,
    ImplicitEventInput,
    ImplicitEventType,
    ImplicitPreferenceEvent,
    InterestedAuthor,
    LibraryFeedback,
    LibraryState,
    LibraryStateInput,
    PreferenceRepository,
    PreferenceScore,
    ReadingDurationBucket,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/library",
    tags=["library"],
    dependencies=[Depends(require_local_session)],
)


class LibraryStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    favorite: bool
    feedback: LibraryFeedback
    feedback_reasons: tuple[str, ...] = Field(max_length=8)
    pinned: bool
    reading_progress: float = Field(ge=0, le=100)
    analysis_state: AnalysisState


class LibraryStateResponse(BaseModel):
    work_id: str
    favorite: bool
    feedback: LibraryFeedback
    feedback_reasons: tuple[str, ...]
    pinned: bool
    reading_progress: float
    analysis_state: AnalysisState
    persisted: bool
    historical_match: bool
    created_at: datetime | None
    updated_at: datetime | None


class ImplicitEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=128)
    event_type: ImplicitEventType
    duration_bucket: ReadingDurationBucket | None = None


class ImplicitEventResponse(BaseModel):
    id: str
    work_id: str
    event_key: str
    event_type: ImplicitEventType
    duration_bucket: ReadingDurationBucket | None
    weight: float
    occurred_at: datetime
    created_at: datetime


class InterestedAuthorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=255)
    active: bool


class InterestedAuthorResponse(BaseModel):
    author_key: str
    display_name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class PreferenceScoreResponse(BaseModel):
    rule_version: str
    score: float
    coverage: float
    confidence: float
    suppressed_by_default: bool
    historical_match: bool
    matched_author_keys: tuple[str, ...]
    behavior_boost: float
    evidence: dict[str, Any]


def _repository(request: Request) -> PreferenceRepository:
    return PreferenceRepository(request.app.state.sessions)


def _not_found(error: KeyError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Work not found: {error.args[0] if error.args else 'unknown'}",
    )


def _state_response(value: LibraryState) -> LibraryStateResponse:
    return LibraryStateResponse(
        work_id=value.work_id,
        favorite=value.favorite,
        feedback=value.feedback,
        feedback_reasons=value.feedback_reasons,
        pinned=value.pinned,
        reading_progress=value.reading_progress,
        analysis_state=value.analysis_state,
        persisted=value.persisted,
        historical_match=value.historical_match,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _event_response(value: ImplicitPreferenceEvent) -> ImplicitEventResponse:
    return ImplicitEventResponse(
        id=value.id,
        work_id=value.work_id,
        event_key=value.event_key,
        event_type=value.event_type,
        duration_bucket=value.duration_bucket,
        weight=value.weight,
        occurred_at=value.occurred_at,
        created_at=value.created_at,
    )


def _author_response(value: InterestedAuthor) -> InterestedAuthorResponse:
    return InterestedAuthorResponse(
        author_key=value.author_key,
        display_name=value.display_name,
        active=value.active,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _score_response(value: PreferenceScore) -> PreferenceScoreResponse:
    return PreferenceScoreResponse(
        rule_version=value.rule_version,
        score=value.score,
        coverage=value.coverage,
        confidence=value.confidence,
        suppressed_by_default=value.suppressed_by_default,
        historical_match=value.historical_match,
        matched_author_keys=value.matched_author_keys,
        behavior_boost=value.behavior_boost,
        evidence=dict(value.evidence),
    )


@router.get("/works/{work_id}", response_model=LibraryStateResponse)
def get_library_state(work_id: str, request: Request) -> LibraryStateResponse:
    try:
        return _state_response(_repository(request).get_library_state(work_id))
    except KeyError as error:
        raise _not_found(error) from error


@router.put(
    "/works/{work_id}",
    response_model=LibraryStateResponse,
    dependencies=[Depends(require_local_mutation)],
)
def update_library_state(
    work_id: str,
    payload: LibraryStateRequest,
    request: Request,
) -> LibraryStateResponse:
    try:
        state = _repository(request).set_library_state(
            work_id,
            LibraryStateInput(
                favorite=payload.favorite,
                feedback=payload.feedback,
                feedback_reasons=payload.feedback_reasons,
                pinned=payload.pinned,
                reading_progress=payload.reading_progress,
                analysis_state=payload.analysis_state,
            ),
        )
    except KeyError as error:
        raise _not_found(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _state_response(state)


@router.post(
    "/works/{work_id}/events",
    status_code=status.HTTP_201_CREATED,
    response_model=ImplicitEventResponse,
    dependencies=[Depends(require_local_mutation)],
)
def record_preference_event(
    work_id: str,
    payload: ImplicitEventRequest,
    request: Request,
) -> ImplicitEventResponse:
    try:
        event = _repository(request).record_implicit_event(
            work_id,
            ImplicitEventInput(
                event_key=payload.event_key,
                event_type=payload.event_type,
                duration_bucket=payload.duration_bucket,
            ),
        )
    except KeyError as error:
        raise _not_found(error) from error
    except ValueError as error:
        code = (
            status.HTTP_409_CONFLICT
            if "event_key conflict" in str(error)
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=str(error)) from error
    return _event_response(event)


@router.get(
    "/works/{work_id}/preference-score",
    response_model=PreferenceScoreResponse,
)
def get_preference_score(work_id: str, request: Request) -> PreferenceScoreResponse:
    try:
        return _score_response(_repository(request).score_work(work_id))
    except KeyError as error:
        raise _not_found(error) from error


@router.get(
    "/interested-authors",
    response_model=list[InterestedAuthorResponse],
)
def list_interested_authors(
    request: Request,
    include_inactive: bool = False,
) -> list[InterestedAuthorResponse]:
    authors = _repository(request).list_interested_authors(
        include_inactive=include_inactive
    )
    return [_author_response(author) for author in authors]


@router.put(
    "/interested-authors/{author_key}",
    response_model=InterestedAuthorResponse,
    dependencies=[Depends(require_local_mutation)],
)
def update_interested_author(
    payload: InterestedAuthorRequest,
    request: Request,
    author_key: Annotated[str, Path(min_length=1, max_length=255)],
) -> InterestedAuthorResponse:
    try:
        author = _repository(request).set_interested_author(
            author_key=author_key,
            display_name=payload.display_name,
            active=payload.active,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _author_response(author)
