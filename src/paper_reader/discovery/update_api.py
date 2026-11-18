"""Read-only paper-update feeds and explicit notification state changes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from paper_reader.discovery.paper_updates import (
    PaperUpdateListItem,
    PaperUpdateRepository,
    PaperUpdateState,
)
from paper_reader.discovery.runs import DiscoveryRunRepository
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1",
    tags=["updates"],
    dependencies=[Depends(require_local_session)],
)


class PaperUpdateWorkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    authors: tuple[str, ...]
    doi: str | None


class PaperUpdateSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    connector_id: str
    external_id: str


class PaperUpdateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None
    state: PaperUpdateState
    changed_fields: tuple[str, ...]
    source_updated_at: datetime | None
    detected_at: datetime
    acted_at: datetime | None
    work: PaperUpdateWorkResponse
    source: PaperUpdateSourceResponse
    has_readable_document: bool

    @classmethod
    def from_domain(cls, update: PaperUpdateListItem) -> PaperUpdateResponse:
        return cls.model_validate(update)


class PaperUpdatePageResponse(BaseModel):
    items: tuple[PaperUpdateResponse, ...]
    total: int
    limit: int
    offset: int


class PaperUpdateStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: PaperUpdateState


class PaperUpdateStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: PaperUpdateState
    acted_at: datetime | None


def _page(
    request: Request,
    *,
    limit: int,
    offset: int,
    run_id: str | None = None,
) -> PaperUpdatePageResponse:
    page = PaperUpdateRepository(request.app.state.sessions).list_visible(
        limit=limit,
        offset=offset,
        run_id=run_id,
    )
    return PaperUpdatePageResponse(
        items=tuple(PaperUpdateResponse.from_domain(update) for update in page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/updates", response_model=PaperUpdatePageResponse)
def list_updates(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperUpdatePageResponse:
    return _page(request, limit=limit, offset=offset)


@router.get("/runs/{run_id}/updates", response_model=PaperUpdatePageResponse)
def list_run_updates(
    run_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperUpdatePageResponse:
    try:
        DiscoveryRunRepository(request.app.state.sessions).get(run_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        ) from error
    return _page(request, limit=limit, offset=offset, run_id=run_id)


@router.put(
    "/updates/{update_id}/state",
    response_model=PaperUpdateStateResponse,
    dependencies=[Depends(require_local_mutation)],
)
def set_update_state(
    update_id: str,
    payload: PaperUpdateStateRequest,
    request: Request,
) -> PaperUpdateStateResponse:
    try:
        update = PaperUpdateRepository(request.app.state.sessions).set_state(
            update_id,
            payload.state,
            now=datetime.now(UTC),
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paper update not found",
        ) from error
    return PaperUpdateStateResponse.model_validate(update)
