"""Authenticated local API for guided setup."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.onboarding.contracts import (
    OnboardingOverview,
    OnboardingSnapshot,
    OnboardingStep,
    OnboardingStepStatus,
    RuntimePathSnapshot,
    RuntimePathUpdate,
    RuntimeRestartResponse,
)
from paper_reader.onboarding.repository import OnboardingTransitionError
from paper_reader.onboarding.reset import DataResetError
from paper_reader.onboarding.service import OnboardingService
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/onboarding",
    tags=["onboarding"],
    dependencies=[Depends(require_local_session)],
)


class OnboardingStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: OnboardingStepStatus


class DataResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation: str = Field(min_length=1, max_length=64)


class DataResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID
    restart_required: Literal[True] = True


def _service(request: Request) -> OnboardingService:
    service = request.app.state.onboarding_service
    if not isinstance(service, OnboardingService):  # pragma: no cover - app invariant
        raise RuntimeError("onboarding service is unavailable")
    return service


async def _overview(request: Request, *, refresh_agents: bool) -> OnboardingOverview:
    return await _service(request).overview(
        agents=request.app.state.agent_registry,
        sources=request.app.state.connector_registry,
        retrieval=request.app.state.vector_capability,
        refresh_agents=refresh_agents,
    )


@router.get("", response_model=OnboardingOverview)
async def onboarding_overview(request: Request) -> OnboardingOverview:
    return await _overview(request, refresh_agents=False)


@router.post(
    "/probe",
    response_model=OnboardingOverview,
    dependencies=[Depends(require_local_mutation)],
)
async def probe_onboarding(request: Request) -> OnboardingOverview:
    return await _overview(request, refresh_agents=True)


@router.put(
    "/runtime-config",
    response_model=RuntimePathSnapshot,
    dependencies=[Depends(require_local_mutation)],
)
def save_runtime_config(
    payload: RuntimePathUpdate,
    request: Request,
) -> RuntimePathSnapshot:
    try:
        return _service(request).save_runtime_paths(payload)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "runtime_config_invalid"},
        ) from error


@router.post(
    "/runtime-config/restart",
    response_model=RuntimeRestartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_local_mutation)],
)
def restart_after_runtime_config(
    background_tasks: BackgroundTasks,
    request: Request,
) -> RuntimeRestartResponse:
    service = _service(request)
    request_id = uuid4()
    background_tasks.add_task(service.request_runtime_config_restart)
    return RuntimeRestartResponse(request_id=request_id)


@router.put(
    "/steps/{step}",
    response_model=OnboardingSnapshot,
    dependencies=[Depends(require_local_mutation)],
)
def save_onboarding_step(
    step: OnboardingStep,
    payload: OnboardingStepRequest,
    request: Request,
) -> OnboardingSnapshot:
    try:
        return _service(request).save_step(step, payload.status)
    except OnboardingTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "onboarding_transition_invalid"},
        ) from error


@router.post(
    "/data-reset",
    response_model=DataResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_local_mutation)],
)
def stage_data_reset(
    payload: DataResetRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> DataResetResponse:
    service = _service(request)
    try:
        pending = service.stage_data_reset(payload.confirmation)
    except DataResetError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "data_reset_confirmation_invalid"},
        ) from error
    background_tasks.add_task(service.request_data_reset_restart)
    return DataResetResponse(request_id=pending.request_id)


__all__ = ["router"]
