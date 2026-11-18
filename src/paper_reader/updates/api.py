"""Local settings API for consent-first release checks."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from paper_reader.security import require_local_mutation, require_local_session
from paper_reader.updates.service import UpdateService, UpdateSnapshot

router = APIRouter(
    prefix="/api/v1/settings/updates",
    dependencies=[Depends(require_local_session)],
    tags=["settings"],
)


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automatic_checks_enabled: bool


def _service(request: Request) -> UpdateService:
    return cast(UpdateService, request.app.state.update_service)


@router.get("", response_model=UpdateSnapshot)
def get_update_settings(request: Request) -> UpdateSnapshot:
    return _service(request).snapshot()


@router.patch("", response_model=UpdateSnapshot, dependencies=[Depends(require_local_mutation)])
def change_update_settings(
    payload: UpdateSettingsRequest,
    request: Request,
) -> UpdateSnapshot:
    # Older v1 clients submit their saved preference here. Return the effective
    # manual-only policy for either value without discarding cached checks.
    return _service(request).snapshot()


@router.post(
    "/check",
    response_model=UpdateSnapshot,
    dependencies=[Depends(require_local_mutation)],
)
async def check_for_updates(request: Request) -> UpdateSnapshot:
    return await _service(request).check(manual=True)


@router.delete(
    "/cache",
    response_model=UpdateSnapshot,
    dependencies=[Depends(require_local_mutation)],
)
def clear_update_cache(request: Request) -> UpdateSnapshot:
    return _service(request).clear_cache()
