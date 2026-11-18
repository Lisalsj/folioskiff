"""Local API for timezone and theme preferences."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request

from paper_reader.security import require_local_mutation, require_local_session
from paper_reader.user_preferences import (
    UserPreferencesService,
    UserPreferencesSnapshot,
    UserPreferencesUpdate,
)

router = APIRouter(
    prefix="/api/v1/settings/preferences",
    dependencies=[Depends(require_local_session)],
    tags=["settings"],
)


def _service(request: Request) -> UserPreferencesService:
    return cast(UserPreferencesService, request.app.state.user_preferences_service)


@router.get("", response_model=UserPreferencesSnapshot)
def get_user_preferences(request: Request) -> UserPreferencesSnapshot:
    return _service(request).snapshot()


@router.put(
    "",
    response_model=UserPreferencesSnapshot,
    dependencies=[Depends(require_local_mutation)],
)
def save_user_preferences(
    payload: UserPreferencesUpdate,
    request: Request,
) -> UserPreferencesSnapshot:
    return _service(request).save(payload)
