"""HTTP workbench for immutable keyword proposals and explicit confirmation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from paper_reader.agents.contracts import ReasoningEffort
from paper_reader.discovery.agent_backend import AgentKeywordSuggestionBackend
from paper_reader.discovery.keywords import (
    CompiledKeywordQuery,
    KeywordRepository,
    KeywordSetDefinition,
    KeywordVersion,
    KeywordVersionNotFoundError,
    UnconfirmedKeywordVersionError,
)
from paper_reader.discovery.preference_context import (
    PreferenceContextService,
    PreferenceSource,
)
from paper_reader.discovery.suggestions import (
    CorpusKeyword,
    KeywordSuggestionBackend,
    KeywordSuggestionGenerationError,
    KeywordSuggestionService,
    ManualKeywordsRequiredError,
)
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/keywords",
    tags=["keywords"],
    dependencies=[Depends(require_local_session)],
)


class KeywordVersionCreate(BaseModel):
    research_intent: str = Field(min_length=1, max_length=10_000)
    definition: KeywordSetDefinition
    parent_version_id: str | None = None
    generator_info: dict[str, Any] = Field(default_factory=dict)


class KeywordSuggestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_intent: str = Field(min_length=1, max_length=10_000)
    mode: Literal["model", "manual"] = "model"
    parent_version_id: str | None = None
    target_count: int = Field(default=18, ge=12, le=24)
    agent_backend_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$",
    )
    agent_model_id: str | None = Field(default=None, max_length=200)
    agent_reasoning_effort: ReasoningEffort | None = None
    use_preference_context: bool = True
    preference_context_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )

    @model_validator(mode="after")
    def context_hash_requires_enabled_context(self) -> KeywordSuggestionCreate:
        if not self.use_preference_context and self.preference_context_sha256 is not None:
            raise ValueError("preference_context_sha256 requires use_preference_context")
        return self

    @model_validator(mode="after")
    def normalize_agent_selection(self) -> KeywordSuggestionCreate:
        if self.agent_model_id is not None:
            normalized = self.agent_model_id.strip()
            if not normalized:
                raise ValueError("agent_model_id cannot be blank")
            if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
                raise ValueError("agent_model_id cannot contain control characters")
            self.agent_model_id = normalized
        return self


class PreferenceContextTermResponse(BaseModel):
    term: str
    weight: float
    sources: tuple[PreferenceSource, ...]


class PreferenceContextResponse(BaseModel):
    version: str
    terms: tuple[PreferenceContextTermResponse, ...]
    source_categories: tuple[PreferenceSource, ...]
    contributing_work_count: int
    context_sha256: str
    empty: bool


class KeywordVersionResponse(BaseModel):
    id: str
    research_intent: str
    parent_version_id: str | None
    definition: KeywordSetDefinition
    change_summary: dict[str, Any]
    generator_info: dict[str, Any]
    created_at: datetime
    confirmed_at: datetime | None

    @classmethod
    def from_domain(cls, version: KeywordVersion) -> KeywordVersionResponse:
        return cls(
            id=version.id,
            research_intent=version.research_intent,
            parent_version_id=version.parent_version_id,
            definition=version.definition,
            change_summary=version.change_summary,
            generator_info=version.generator_info,
            created_at=version.created_at,
            confirmed_at=version.confirmed_at,
        )


class CompiledKeywordResponse(BaseModel):
    version_id: str
    boolean_expression: str
    boost_terms: tuple[str, ...]

    @classmethod
    def from_domain(cls, query: CompiledKeywordQuery) -> CompiledKeywordResponse:
        return cls(
            version_id=query.version_id,
            boolean_expression=query.boolean_expression,
            boost_terms=query.boost_terms,
        )


def repository(request: Request) -> KeywordRepository:
    return KeywordRepository(request.app.state.sessions)


KeywordRepo = Annotated[KeywordRepository, Depends(repository)]


@router.get(
    "/preference-context",
    response_model=PreferenceContextResponse,
)
def preference_context(request: Request) -> PreferenceContextResponse:
    context = PreferenceContextService(request.app.state.sessions).current()
    return PreferenceContextResponse.model_validate(context.as_dict())


@router.post(
    "/suggestions",
    status_code=status.HTTP_201_CREATED,
    response_model=KeywordVersionResponse,
    dependencies=[Depends(require_local_mutation)],
)
async def suggest_keywords(
    payload: KeywordSuggestionCreate,
    request: Request,
    repo: KeywordRepo,
) -> KeywordVersionResponse:
    registry = getattr(request.app.state, "agent_registry", None)
    preferences = getattr(request.app.state, "user_preferences_service", None)
    backend: KeywordSuggestionBackend | None = None
    if payload.mode == "model" and registry is not None and preferences is not None:
        backend = AgentKeywordSuggestionBackend(
            registry=registry,
            preferences=preferences,
            backend_id=payload.agent_backend_id,
            model_id=payload.agent_model_id,
            use_default_model="agent_model_id" not in payload.model_fields_set,
            reasoning_effort=payload.agent_reasoning_effort,
        )
    elif payload.mode == "manual":
        # A plugin or test may supply a deterministic manual-mode backend;
        # otherwise the built-in rules are used explicitly.
        backend = getattr(request.app.state, "keyword_suggestion_backend", None)
    service = KeywordSuggestionService(
        repository=repo,
        backend=backend,
        allow_local_fallback=payload.mode == "manual",
    )
    preference = (
        PreferenceContextService(request.app.state.sessions).current()
        if payload.use_preference_context
        else None
    )
    if (
        preference is not None
        and payload.preference_context_sha256 is not None
        and preference.context_sha256 != payload.preference_context_sha256
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "preference_context_stale",
                "message": "Preference context changed; review it before retrying",
                "current_sha256": preference.context_sha256,
            },
        )
    try:
        version = await service.suggest(
            research_intent=payload.research_intent,
            target_count=payload.target_count,
            parent_version_id=payload.parent_version_id,
            preference_terms=(
                tuple(
                    CorpusKeyword(term=item.term, weight=item.weight) for item in preference.terms
                )
                if preference is not None
                else ()
            ),
            preference_context_enabled=preference is not None,
            preference_context_sha256=(
                preference.context_sha256 if preference is not None else None
            ),
        )
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent not found",
        ) from error
    except ManualKeywordsRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "manual_configuration_required",
                "message": str(error),
            },
        ) from error
    except KeywordSuggestionGenerationError as error:
        detail = {
            "code": error.code,
            "message": error.message,
            "backend_id": error.backend_id,
        }
        if error.requested_model_id is not None:
            detail["requested_model_id"] = error.requested_model_id
        raise HTTPException(
            status_code=(
                status.HTTP_504_GATEWAY_TIMEOUT
                if error.code == "timeout"
                else status.HTTP_502_BAD_GATEWAY
                if error.code in {"invalid_output", "output_limit", "protocol"}
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=detail,
        ) from error
    return KeywordVersionResponse.from_domain(version)


@router.post(
    "/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=KeywordVersionResponse,
    dependencies=[Depends(require_local_mutation)],
)
def create_keyword_version(
    payload: KeywordVersionCreate,
    repo: KeywordRepo,
) -> KeywordVersionResponse:
    try:
        version = repo.create_version(
            research_intent=payload.research_intent,
            definition=payload.definition,
            parent_version_id=payload.parent_version_id,
            generator_info=payload.generator_info,
        )
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent not found",
        ) from error
    return KeywordVersionResponse.from_domain(version)


@router.get("/versions/{version_id}", response_model=KeywordVersionResponse)
def get_keyword_version(version_id: str, repo: KeywordRepo) -> KeywordVersionResponse:
    try:
        return KeywordVersionResponse.from_domain(repo.get(version_id))
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        ) from error


@router.post(
    "/versions/{version_id}/confirm",
    response_model=KeywordVersionResponse,
    dependencies=[Depends(require_local_mutation)],
)
def confirm_keyword_version(version_id: str, repo: KeywordRepo) -> KeywordVersionResponse:
    try:
        return KeywordVersionResponse.from_domain(repo.confirm(version_id))
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        ) from error


@router.get("/versions/{version_id}/compiled", response_model=CompiledKeywordResponse)
def compile_keyword_version(version_id: str, repo: KeywordRepo) -> CompiledKeywordResponse:
    try:
        return CompiledKeywordResponse.from_domain(repo.compile(version_id))
    except KeywordVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        ) from error
    except UnconfirmedKeywordVersionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
