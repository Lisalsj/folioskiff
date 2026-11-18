"""Storage-free API v1 compatibility views over the unified paper Assistant."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from paper_reader.agents.api import AnalysisJobResponse
from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.agents.evidence import (
    EvidenceBundle,
    EvidenceConflictError,
    EvidenceUnavailableError,
)
from paper_reader.agents.report_schema import EvidenceReference, ReferenceKind
from paper_reader.agents.templates import AgentSettingsRepository
from paper_reader.assistant.contracts import (
    AgentSelection,
    AssistantMode,
    AssistantTaskCommand,
    ContextScope,
    ContextSelection,
    NetworkPolicy,
)
from paper_reader.assistant.repository import (
    AssistantCheckpoint,
    AssistantFragment,
    AssistantMessage,
    AssistantRepositoryError,
    AssistantSession,
    AssistantTask,
)
from paper_reader.assistant.service import (
    AssistantExecutionError,
    AssistantNotReadyError,
    AssistantService,
)
from paper_reader.jobs.models import JobRecord
from paper_reader.security import require_local_mutation, require_local_session

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}
_COMPATIBILITY_OPENAPI = {"x-paper-reader-compatibility-adapter": True}


def _protect_private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


conversation_router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["conversations"],
    dependencies=[
        Depends(require_local_session),
        Depends(_protect_private_response),
    ],
)


class ConversationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    document_version_id: str | None = Field(default=None, max_length=80)
    backend_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=200)
    reasoning_effort: ReasoningEffort | None = None
    service_tier: ServiceTier | None = None

    @field_validator("document_version_id", "backend_id", "model_id")
    @classmethod
    def _optional_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("identifier cannot be blank")
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("identifier cannot contain control characters")
        return value


class ConversationReplyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=20_000)
    selection: ConversationSelection = Field(default_factory=ConversationSelection)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message cannot be blank")
        return normalized


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    document_version_id: str | None
    document_content_sha256: str | None
    evidence_sha256: str | None
    evidence_manifest: dict[str, Any]
    backend_id: str | None
    requested_model_id: str | None
    resolved_model_id: str | None
    status: str
    usage: dict[str, Any]
    citations: tuple[EvidenceReference, ...]
    job_id: str | None
    reply_to_message_id: str | None
    created_at: datetime


class ConversationCheckpointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    source_start_sequence: int
    source_end_sequence: int
    summary: str
    summary_sha256: str
    backend_id: str
    model_id: str | None
    estimated_tokens: int
    estimated_characters: int
    created_at: datetime


class ConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    work_id: str
    active_backend_id: str
    active_model_id: str | None
    created_at: datetime
    updated_at: datetime
    messages: tuple[ConversationMessageResponse, ...]
    checkpoints: tuple[ConversationCheckpointResponse, ...]


class ConversationFragmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    sequence: int
    kind: str
    text: str
    created_at: datetime


class ConversationCompatibilityAdapter:
    """Translate documented conversation fields without owning persistence."""

    def __init__(
        self,
        *,
        assistant: AssistantService,
        settings: AgentSettingsRepository,
    ) -> None:
        self.assistant = assistant
        self.repository = assistant.repository
        self.settings = settings

    async def create_reply_job(
        self,
        work_id: str,
        command: ConversationReplyCommand,
        *,
        idempotency_key: str,
    ) -> JobRecord:
        defaults = self.settings.get()
        selection = command.selection
        document_version_id = self.repository.document_version_id_for_work(
            work_id,
            selection.document_version_id,
        )
        assistant_command = AssistantTaskCommand(
            mode=AssistantMode.ASK,
            message=command.message,
            context=ContextSelection(
                document_version_id=document_version_id,
                scope=ContextScope.FULL_DOCUMENT,
            ),
            agent=AgentSelection(
                backend_id=selection.backend_id or defaults.default_backend_id,
                model_id=selection.model_id or defaults.chat_model_id,
                reasoning_effort=(
                    selection.reasoning_effort
                    or ReasoningEffort(defaults.chat_reasoning)
                ),
                service_tier=(
                    selection.service_tier or ServiceTier(defaults.service_tier)
                ),
            ),
            network_policy=NetworkPolicy.LOCAL_ONLY,
            output_language="zh-CN",
        )
        return await self.assistant.create_task(
            work_id,
            assistant_command,
            idempotency_key=idempotency_key,
        )

    def for_work(self, work_id: str) -> ConversationResponse | None:
        assistant_session = self.repository.find_session_for_work(work_id)
        if assistant_session is None:
            return None
        return self._response(assistant_session)

    def by_id(self, conversation_id: str) -> ConversationResponse | None:
        assistant_session = self.repository.session(conversation_id)
        if assistant_session is None:
            return None
        return self._response(assistant_session)

    def fragments(self, job_id: str) -> tuple[ConversationFragmentResponse, ...]:
        task = self.repository.task_for_job(job_id)
        if task is None:
            return ()
        return tuple(
            _fragment_response(job_id, item)
            for item in self.repository.fragments(task.id)
        )

    def _response(self, assistant_session: AssistantSession) -> ConversationResponse:
        tasks = self.repository.tasks(assistant_session.id)
        tasks_by_id = {task.id: task for task in tasks}
        defaults = self.settings.get()
        active = tasks[-1] if tasks else None
        return ConversationResponse(
            id=assistant_session.id,
            work_id=assistant_session.work_id,
            active_backend_id=(
                active.backend_id if active is not None else defaults.default_backend_id
            ),
            active_model_id=(
                active.requested_model_id if active is not None else defaults.chat_model_id
            ),
            created_at=assistant_session.created_at,
            updated_at=assistant_session.updated_at,
            messages=tuple(
                self._message_response(message, tasks_by_id[message.task_id])
                for message in self.repository.messages(assistant_session.id)
            ),
            checkpoints=tuple(
                _checkpoint_response(item)
                for item in self.repository.checkpoints(assistant_session.id)
            ),
        )

    def _message_response(
        self,
        message: AssistantMessage,
        task: AssistantTask,
    ) -> ConversationMessageResponse:
        snapshot = self.repository.snapshot(task.context_snapshot_id)
        evidence = EvidenceBundle.model_validate(snapshot.evidence)
        runs = self.repository.runs(task.id)
        latest_run = runs[-1] if runs else None
        generated = message.role != "user"
        return ConversationMessageResponse(
            id=message.id,
            conversation_id=message.session_id,
            sequence=message.sequence,
            role=message.role,
            content=message.content,
            document_version_id=snapshot.document_version_id,
            document_content_sha256=snapshot.document_content_sha256,
            evidence_sha256=snapshot.evidence_sha256,
            evidence_manifest=_legacy_evidence_manifest(evidence),
            backend_id=task.backend_id,
            requested_model_id=task.requested_model_id,
            resolved_model_id=(
                latest_run.resolved_model_id
                if generated and latest_run is not None
                else None
            ),
            status=message.status,
            usage=(
                dict(latest_run.usage)
                if generated and latest_run is not None
                else {}
            ),
            citations=tuple(
                _legacy_reference(item, evidence) for item in message.citations
            ),
            job_id=message.job_id,
            reply_to_message_id=message.reply_to_message_id,
            created_at=message.created_at,
        )


def _adapter(request: Request) -> ConversationCompatibilityAdapter:
    assistant = cast(AssistantService | None, request.app.state.assistant_service)
    if assistant is None:  # pragma: no cover - lifespan initializes before requests
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Assistant service is not ready",
        )
    return ConversationCompatibilityAdapter(
        assistant=assistant,
        settings=AgentSettingsRepository(request.app.state.sessions),
    )


@conversation_router.get(
    "/works/{work_id}",
    response_model=ConversationResponse | None,
    openapi_extra=_COMPATIBILITY_OPENAPI,
)
def get_work_conversation(
    work_id: str,
    request: Request,
) -> ConversationResponse | None:
    try:
        return _adapter(request).for_work(work_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work was not found",
        ) from error


@conversation_router.get(
    "/jobs/{job_id}/fragments",
    response_model=tuple[ConversationFragmentResponse, ...],
    openapi_extra=_COMPATIBILITY_OPENAPI,
)
def list_conversation_fragments(
    job_id: str,
    request: Request,
) -> tuple[ConversationFragmentResponse, ...]:
    return _adapter(request).fragments(job_id)


@conversation_router.post(
    "/works/{work_id}/replies",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisJobResponse,
    dependencies=[Depends(require_local_mutation)],
    openapi_extra=_COMPATIBILITY_OPENAPI,
)
async def create_conversation_reply(
    work_id: str,
    command: ConversationReplyCommand,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ],
) -> AnalysisJobResponse:
    adapter = _adapter(request)
    try:
        job = await adapter.create_reply_job(
            work_id,
            command,
            idempotency_key=idempotency_key,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work, document, or Agent backend was not found",
        ) from error
    except EvidenceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (
        AssistantExecutionError,
        AssistantNotReadyError,
        AssistantRepositoryError,
        EvidenceConflictError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if job.status in {"queued", "running"}:
        request.app.state.background_tasks.start(
            f"assistant-reply:{job.id}",
            lambda: adapter.assistant.execute(job.id),
        )
    return AnalysisJobResponse.model_validate(job)


@conversation_router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    openapi_extra=_COMPATIBILITY_OPENAPI,
)
def get_conversation(
    conversation_id: str,
    request: Request,
) -> ConversationResponse:
    try:
        conversation = _adapter(request).by_id(conversation_id)
    except ValueError:
        conversation = None
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paper conversation was not found",
        )
    return conversation


def _checkpoint_response(item: AssistantCheckpoint) -> ConversationCheckpointResponse:
    return ConversationCheckpointResponse(
        id=item.id,
        conversation_id=item.session_id,
        source_start_sequence=item.source_start_sequence,
        source_end_sequence=item.through_sequence,
        summary=item.summary,
        summary_sha256=item.summary_sha256,
        backend_id=item.backend_id,
        model_id=item.model_id,
        estimated_tokens=item.estimated_tokens,
        estimated_characters=item.estimated_characters,
        created_at=item.created_at,
    )


def _fragment_response(
    job_id: str,
    item: AssistantFragment,
) -> ConversationFragmentResponse:
    return ConversationFragmentResponse(
        job_id=job_id,
        sequence=item.sequence,
        kind=item.kind,
        text=item.text,
        created_at=item.created_at,
    )


def _legacy_reference(
    item: dict[str, Any],
    evidence: EvidenceBundle,
) -> EvidenceReference:
    evidence_id = str(item["evidence_id"])
    kind = item.get("kind")
    if kind is None:
        if evidence_id == "abstract:work":
            kind = "abstract"
        elif evidence_id.startswith("annotation:"):
            kind = "annotation"
        else:
            kind = "page"
    page_number = item.get("page_number")
    if page_number is None and kind == "annotation":
        annotation = next(
            (
                candidate
                for candidate in evidence.annotations
                if candidate.evidence_id == evidence_id
            ),
            None,
        )
        page_number = annotation.page_number if annotation is not None else None
    elif page_number is None and kind in {"page", "figure", "table", "equation"}:
        page = next(
            (
                candidate
                for candidate in evidence.pages
                if candidate.evidence_id == evidence_id
            ),
            None,
        )
        page_number = page.page_number if page is not None else None
    return EvidenceReference(
        evidence_id=evidence_id,
        kind=cast(ReferenceKind, kind),
        page_number=page_number,
        locator=item.get("locator"),
        url=None,
        external_title=None,
    )


def _legacy_evidence_manifest(evidence: EvidenceBundle) -> dict[str, Any]:
    return {
        "schema_version": evidence.schema_version,
        "work_id": evidence.work.work_id,
        "document": (
            evidence.document.model_dump(mode="json")
            if evidence.document is not None
            else None
        ),
        "evidence_level": evidence.evidence_level.value,
        "evidence_sha256": evidence.evidence_sha256,
        "pages": [
            {
                "evidence_id": item.evidence_id,
                "page_number": item.page_number,
                "source": item.source,
                "source_text_sha256": item.source_text_sha256,
            }
            for item in evidence.pages
        ],
        "annotations": [
            {
                "evidence_id": item.evidence_id,
                "annotation_id": item.annotation_id,
                "page_number": item.page_number,
            }
            for item in evidence.annotations
        ],
        "abstract_available": bool(evidence.abstract_evidence or evidence.work.abstract),
        "coverage": evidence.coverage.model_dump(mode="json"),
    }


__all__ = [
    "ConversationCompatibilityAdapter",
    "ConversationReplyCommand",
    "ConversationResponse",
    "conversation_router",
]
