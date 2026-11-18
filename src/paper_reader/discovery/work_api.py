"""Canonical Work identity and explicit merge-review HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.discovery.identity import (
    IdentityRepository,
    MergeConflictError,
    MergeReview,
    MergeReviewAlreadyDecidedError,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/works",
    tags=["works"],
    dependencies=[Depends(require_local_session)],
)

ReviewStatus = Literal["pending", "merged", "kept_separate", "superseded"]


class WorkIdentityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None
    status: str
    source_count: int


class MergeReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_work: WorkIdentityResponse
    matched_work: WorkIdentityResponse
    similarity: float
    evidence: dict[str, Any]
    status: str
    decision_note: str | None
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def from_domain(cls, review: MergeReview) -> MergeReviewResponse:
        return cls.model_validate(review)


class MergeReviewDecision(BaseModel):
    decision: Literal["merge", "keep_separate"]
    note: str | None = Field(default=None, max_length=2_000)


@router.get("/merge-reviews", response_model=list[MergeReviewResponse])
def list_merge_reviews(
    request: Request,
    review_status: Annotated[ReviewStatus, Query(alias="status")] = "pending",
) -> list[MergeReviewResponse]:
    reviews = IdentityRepository(request.app.state.sessions).list_merge_reviews(
        status=review_status
    )
    return [MergeReviewResponse.from_domain(review) for review in reviews]


@router.post(
    "/merge-reviews/{review_id}/decision",
    response_model=MergeReviewResponse,
    dependencies=[Depends(require_local_mutation)],
)
def decide_merge_review(
    review_id: str,
    payload: MergeReviewDecision,
    request: Request,
) -> MergeReviewResponse:
    try:
        review = IdentityRepository(request.app.state.sessions).decide_merge_review(
            review_id,
            decision=payload.decision,
            note=payload.note,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Merge review not found",
        ) from error
    except (MergeReviewAlreadyDecidedError, MergeConflictError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return MergeReviewResponse.from_domain(review)
