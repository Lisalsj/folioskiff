"""Private local API for confirmed, durable related-resource exploration."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from paper_reader.agents.evidence import EvidenceError
from paper_reader.assistant.contracts import AgentSelection, ContextSelection
from paper_reader.assistant.repository import AssistantConflictError
from paper_reader.related_resources.contracts import RelatedSearchIntent
from paper_reader.related_resources.intent import RelatedIntentNotReadyError
from paper_reader.related_resources.repository import (
    RelatedIntentConflict,
    RelatedIntentStateError,
)
from paper_reader.related_resources.service import (
    RelatedPaperSave,
    RelatedResourceService,
    RelatedResourceSetView,
    RelatedRunConflict,
    RelatedRunOptions,
    RelatedRunStart,
)
from paper_reader.security import require_local_mutation, require_local_session

_PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
}


def _protect_private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


router = APIRouter(
    prefix="/api/v1/related-resources",
    tags=["related-resources"],
    dependencies=[Depends(require_local_session), Depends(_protect_private_response)],
)


class RelatedDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context_snapshot_id: str | None = Field(default=None, min_length=1, max_length=80)
    context: ContextSelection | None = None

    @model_validator(mode="after")
    def _exact_context_source(self) -> RelatedDraftRequest:
        if (self.context_snapshot_id is None) == (self.context is None):
            raise ValueError("provide exactly one context snapshot or reader context")
        return self


class RelatedRefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction: str = Field(min_length=1, max_length=10_000)
    agent: AgentSelection


class RelatedIntentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=1)
    intent: RelatedSearchIntent
    confirm: bool = False


def _service(request: Request) -> RelatedResourceService:
    service = cast(
        RelatedResourceService | None,
        request.app.state.related_resource_service,
    )
    if service is None:  # pragma: no cover - lifespan initializes before requests
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Related-resource service is not ready",
        )
    return service


@router.post(
    "/works/{work_id}/draft",
    status_code=status.HTTP_201_CREATED,
    response_model=RelatedSearchIntent,
    dependencies=[Depends(require_local_mutation)],
)
def draft_related_intent(
    work_id: str,
    command: RelatedDraftRequest,
    request: Request,
) -> RelatedSearchIntent:
    try:
        return _service(request).draft(
            work_id,
            context_snapshot_id=command.context_snapshot_id,
            context=command.context,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="Related-search context was not found"
        ) from error
    except (
        AssistantConflictError,
        EvidenceError,
        RelatedIntentConflict,
        RelatedIntentStateError,
        ValueError,
    ) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/{set_id}/refine",
    response_model=RelatedSearchIntent,
    dependencies=[Depends(require_local_mutation)],
)
async def refine_related_intent(
    set_id: str,
    command: RelatedRefineRequest,
    request: Request,
) -> RelatedSearchIntent:
    try:
        return await _service(request).intent_service.refine(
            set_id,
            command.instruction,
            command.agent,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Related-resource set was not found") from error
    except RelatedIntentNotReadyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (RelatedIntentConflict, RelatedIntentStateError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put(
    "/{set_id}/intent",
    response_model=RelatedSearchIntent,
    dependencies=[Depends(require_local_mutation)],
)
def update_related_intent(
    set_id: str,
    command: RelatedIntentUpdateRequest,
    request: Request,
) -> RelatedSearchIntent:
    service = _service(request)
    try:
        if command.confirm:
            return service.intent_service.confirm(
                set_id,
                revision=command.expected_revision,
                intent=command.intent,
            )
        return service.intents.revise(
            set_id,
            expected_revision=command.expected_revision,
            intent=command.intent,
            origin="manual_edit",
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Related-resource set was not found") from error
    except (RelatedIntentConflict, RelatedIntentStateError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/{set_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RelatedRunStart,
    dependencies=[Depends(require_local_mutation)],
)
async def run_related_search(
    set_id: str,
    options: RelatedRunOptions,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> RelatedRunStart:
    service = _service(request)
    try:
        started = service.start(
            set_id,
            options,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Related-resource set was not found") from error
    except (RelatedRunConflict, RelatedIntentConflict, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if started.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"related-search:{started.job_id}",
            lambda: service.execute(started.job_id),
        )
    return started


@router.get("/{set_id}", response_model=RelatedResourceSetView)
def get_related_resource_set(
    set_id: str,
    request: Request,
) -> RelatedResourceSetView:
    try:
        return _service(request).get(set_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Related-resource set was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/{set_id}/papers/{candidate_id}/save",
    response_model=RelatedPaperSave,
    dependencies=[Depends(require_local_mutation)],
)
def save_related_paper(
    set_id: str,
    candidate_id: str,
    request: Request,
    _idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> RelatedPaperSave:
    try:
        return _service(request).save_paper(set_id, candidate_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Related paper was not found") from error
    except (RelatedRunConflict, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


__all__ = ["router"]
