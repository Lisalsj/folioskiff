from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentCapability,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentTaskKind,
    AgentUsage,
    ModelCatalog,
)
from paper_reader.agents.evidence import EvidenceBundleBuilder
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.assistant.context import AssistantContextBuilder
from paper_reader.assistant.contracts import (
    AgentSelection,
    AssistantTaskStatus,
    ContextScope,
    ContextSelection,
)
from paper_reader.assistant.repository import AssistantRepository
from paper_reader.jobs.repository import JobRepository
from paper_reader.reader.models import DocumentPageTextRecord
from paper_reader.translations.contracts import (
    TranslationCommand,
    TranslationStatus,
    TranslationTermInput,
)
from paper_reader.translations.repository import TranslationRepository
from paper_reader.translations.service import TranslationService, TranslationStart
from tests.translations.conftest import TranslationRepositoryContext


class RecordingTranslationBackend:
    def __init__(self) -> None:
        self.capability = AgentCapability(
            backend_id="codex_cli",
            display_name="Translation fixture",
            location=AgentLocation.LOCAL,
            probe_code=AgentProbeCode.READY,
            installed=True,
            authenticated=True,
            available=True,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=False,
            supports_external_research=False,
        )
        self.requests: list[AgentRequest] = []
        self.outputs: list[Mapping[str, Any] | Exception] = []
        self.output_factory: Callable[[AgentRequest], Mapping[str, Any]] | None = None
        self.generated_input_ids: list[tuple[str, ...]] = []

    async def discover_models(self) -> ModelCatalog:
        return ModelCatalog(
            models=(AgentModel(model_id="fixture-model", display_name="Fixture model"),),
            supports_manual_model_id=True,
            supports_default_model=True,
        )

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: Callable[[AgentFragment], Awaitable[None]],
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        self.requests.append(request)
        if cancelled():
            raise AgentBackendError(AgentErrorCode.CANCELLED, "Translation fixture cancelled")
        if self.outputs:
            output = self.outputs.pop(0)
        elif self.output_factory is not None:
            output = self.output_factory(request)
        else:
            raise AssertionError("test must provide one structured translation output per call")
        if isinstance(output, Exception):
            raise output
        self.generated_input_ids.append(tuple(request.structured_input_ids))
        await on_fragment(AgentFragment(index=0, kind="text", text="uncommitted fixture fragment"))
        return AgentResult(
            raw_output='{"segments":[]}',
            structured_output=output,
            resolved_model_id=request.model_id or "fixture-model",
            usage=AgentUsage(input_tokens=80, output_tokens=30),
        )


class PersistOrderJobs:
    def __init__(self, delegate: JobRepository, repository: TranslationRepository) -> None:
        self._delegate = delegate
        self._repository = repository
        self.batch_event_checks = 0
        self.cancel_after_first_batch_job_id: str | None = None
        self._cancelled_after_batch = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def append_event(self, **kwargs: Any) -> Any:
        if kwargs["event_type"] == "translation.batch.succeeded":
            artifact_id = kwargs["payload"]["artifact_id"]
            committed = {
                segment.segment_id: segment.status
                for segment in self._repository.segments(artifact_id)
            }
            for segment_id in kwargs["payload"]["segment_ids"]:
                assert committed[segment_id] == "succeeded"
            self.batch_event_checks += 1
        event = self._delegate.append_event(**kwargs)
        if (
            kwargs["event_type"] == "translation.batch.succeeded"
            and kwargs["job_id"] == self.cancel_after_first_batch_job_id
            and not self._cancelled_after_batch
        ):
            self._delegate.request_cancel(kwargs["job_id"])
            self._cancelled_after_batch = True
        return event


@dataclass(slots=True)
class ServiceContext:
    source: TranslationRepositoryContext
    repository: TranslationRepository
    assistant_repository: AssistantRepository
    jobs: PersistOrderJobs
    backend: RecordingTranslationBackend
    service: TranslationService

    async def create(self, key: str = "translation-service-1") -> TranslationStart:
        return await self.service.create(_page_command(), key)

    def valid_output(self, artifact_id: str) -> dict[str, object]:
        return {
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "translated_text": f"译文：{segment.protected_text}",
                    "uncertain_terms": [],
                }
                for segment in self.repository.pending_segments(artifact_id)
            ]
        }


@pytest.fixture
def service_context(
    translation_repository_context: TranslationRepositoryContext,
) -> ServiceContext:
    source = translation_repository_context
    repository = TranslationRepository(source.sessions)
    assistant_repository = AssistantRepository(source.sessions)
    backend = RecordingTranslationBackend()
    jobs = PersistOrderJobs(JobRepository(source.sessions), repository)
    builder = AssistantContextBuilder(
        sessions=source.sessions,
        evidence=EvidenceBundleBuilder(sessions=source.sessions, store=source.store),
        repository=assistant_repository,
    )
    service = TranslationService(
        jobs=jobs,
        repository=repository,
        assistant_repository=assistant_repository,
        context=builder,
        backends=AgentBackendRegistry((backend,)),
        segment_character_limit=256,
    )
    return ServiceContext(
        source=source,
        repository=repository,
        assistant_repository=assistant_repository,
        jobs=jobs,
        backend=backend,
        service=service,
    )


def _page_command() -> TranslationCommand:
    return TranslationCommand(
        context=ContextSelection(
            document_version_id="document-1",
            scope=ContextScope.CURRENT_PAGE,
            page_number=1,
        ),
        agent=AgentSelection(
            backend_id="codex_cli",
            model_id="fixture-model",
        ),
        user_terminology=(TranslationTermInput(source_term="agent", target_term="智能体"),),
    )


@pytest.mark.asyncio
async def test_translation_request_is_local_only_and_uses_exact_segment_ids(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create()
    context.backend.outputs = [context.valid_output(start.artifact_id)]

    await context.service.execute(start.job_id)

    request = context.backend.requests[0]
    assert start.quick is True
    assert request.task_kind is AgentTaskKind.ASSISTANT_TRANSLATE
    assert request.external_research is False
    assert set(request.structured_input_ids) == {"segment-000001", "segment-000002"}
    assert context.jobs.batch_event_checks == 1
    assert context.repository.get(start.artifact_id).status is TranslationStatus.SUCCEEDED
    assert context.jobs.get(start.job_id).status == "succeeded"


@pytest.mark.asyncio
async def test_missing_formula_placeholder_fails_batch_without_publishing(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-missing-placeholder")
    valid = context.valid_output(start.artifact_id)
    first = valid["segments"][0]
    token = next(iter(context.repository.segments(start.artifact_id)[0].protected_map))
    first["translated_text"] = first["translated_text"].replace(token, "")
    context.backend.outputs = [valid, valid]

    await context.service.execute(start.job_id)

    assert len(context.backend.requests) == 2
    assert context.backend.requests[1].task_id.endswith("-repair")
    assert context.jobs.get(start.job_id).status == "failed"
    assert context.repository.get(start.artifact_id).status is TranslationStatus.DRAFT
    assert all(
        segment.translated_text is None
        for segment in context.repository.segments(start.artifact_id)
    )


@pytest.mark.asyncio
async def test_invalid_first_output_gets_one_repair_then_commits(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-one-repair")
    valid = context.valid_output(start.artifact_id)
    invalid = {"segments": valid["segments"][:-1]}
    context.backend.outputs = [invalid, valid]

    await context.service.execute(start.job_id)

    assert len(context.backend.requests) == 2
    assert context.repository.get(start.artifact_id).status is TranslationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_create_is_idempotent_and_does_not_publish_chat_messages(
    service_context: ServiceContext,
) -> None:
    context = service_context

    first = await context.create("translation-idempotent")
    replay = await context.create("translation-idempotent")

    assert replay == first
    assert context.jobs.get(first.job_id).kind == "translation.generate"
    assert context.assistant_repository.messages_for_work("work-1") == ()
    assert context.repository.terms(first.artifact_id)[0].target_term == "智能体"


@pytest.mark.asyncio
async def test_translation_persists_legitimate_multiline_output(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-multiline")
    output = context.valid_output(start.artifact_id)
    output["segments"][0]["translated_text"] += "\n第二行。"
    context.backend.outputs = [output]

    await context.service.execute(start.job_id)

    assert context.jobs.get(start.job_id).status == "succeeded"
    assert "\n第二行。" in context.repository.segments(start.artifact_id)[0].translated_text


@pytest.mark.asyncio
async def test_multiline_backend_diagnostic_fails_safely_without_escaping_worker(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-multiline-diagnostic")
    context.backend.outputs = [
        AgentBackendError(
            AgentErrorCode.PROCESS,
            "Agent process failed",
            diagnostic="first line\nsecond line",
        )
    ]

    await context.service.execute(start.job_id)

    assert context.jobs.get(start.job_id).status == "failed"
    stored = context.repository.segments(start.artifact_id)
    assert stored[0].status == "failed"
    assert "second line" in (stored[0].diagnostic or "")


@pytest.mark.asyncio
async def test_cancelled_translation_never_calls_agent_backend(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-cancelled")
    context.jobs.request_cancel(start.job_id)

    await context.service.execute(start.job_id)

    assert context.backend.requests == []
    assert context.jobs.get(start.job_id).status == "cancelled"
    assert context.repository.get(start.artifact_id).status is TranslationStatus.CANCELLED


@pytest.mark.asyncio
async def test_restart_closes_interrupted_run_and_translates_only_pending_segments(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-restart-pending")
    context.repository.start(start.artifact_id)
    interrupted = context.assistant_repository.create_run(start.task_id)
    context.assistant_repository.start_run(
        interrupted.id,
        request_sha256="a" * 64,
        prompt_sha256="b" * 64,
    )
    first, second = context.repository.segments(start.artifact_id)
    context.repository.save_batch(
        start.artifact_id,
        {first.segment_id: f"Committed translation: {first.source_text}"},
    )
    context.backend.outputs = [context.valid_output(start.artifact_id)]

    await context.service.execute(start.job_id)

    request = context.backend.requests[0]
    assert request.structured_input_ids == (second.segment_id,)
    assert "-attempt-0002-batch-0001" in request.task_id
    runs = context.assistant_repository.runs(start.task_id)
    assert [run.status for run in runs] == [
        AssistantTaskStatus.FAILED,
        AssistantTaskStatus.SUCCEEDED,
    ]
    assert runs[0].error_code == "translation_interrupted"
    assert context.repository.get(start.artifact_id).status is TranslationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_restart_after_last_batch_never_leaves_artifact_running_without_a_run(
    service_context: ServiceContext,
) -> None:
    context = service_context
    start = await context.create("translation-restart-final-state")
    context.repository.start(start.artifact_id)
    interrupted = context.assistant_repository.create_run(start.task_id)
    context.assistant_repository.start_run(
        interrupted.id,
        request_sha256="c" * 64,
        prompt_sha256="d" * 64,
    )
    context.repository.save_batch(
        start.artifact_id,
        {
            segment.segment_id: f"Committed translation: {segment.source_text}"
            for segment in context.repository.segments(start.artifact_id)
        },
    )

    await context.service.execute(start.job_id)

    assert context.backend.requests == []
    assert context.jobs.get(start.job_id).status == "failed"
    assert context.repository.get(start.artifact_id).status is TranslationStatus.PARTIAL


@pytest.mark.asyncio
async def test_cancel_after_committed_batch_resumes_only_remaining_exact_segment_ids(
    service_context: ServiceContext,
) -> None:
    context = service_context
    paragraphs = [
        (
            f"Segment {index:02d} describes diffusion agent evidence with stable "
            f"variable $x_{index}$ and citation (Smith, 2026)."
        )
        for index in range(1, 42)
    ]
    page_text = "\n\n".join(paragraphs)
    with context.source.sessions() as session:
        page = session.get(DocumentPageTextRecord, ("document-1", 1))
        assert page is not None
        page.native_text = page_text
        page.native_blocks = [
            {
                "x0": 20.0,
                "y0": float(20 + index * 12),
                "x1": 560.0,
                "y1": float(30 + index * 12),
                "text": paragraph,
                "block_number": index,
            }
            for index, paragraph in enumerate(paragraphs, start=1)
        ]
        page.native_character_count = len(page_text)
        session.commit()

    start = await context.create("translation-cancel-after-committed-batch")
    source_segments = {
        segment.segment_id: segment for segment in context.repository.segments(start.artifact_id)
    }
    assert len(source_segments) == 41

    def exact_output(request: AgentRequest) -> Mapping[str, Any]:
        return {
            "segments": [
                {
                    "segment_id": segment_id,
                    "translated_text": f"确定性译文：{source_segments[segment_id].protected_text}",
                    "uncertain_terms": [],
                }
                for segment_id in request.structured_input_ids
            ]
        }

    context.backend.output_factory = exact_output
    context.jobs.cancel_after_first_batch_job_id = start.job_id

    await context.service.execute(start.job_id)

    committed = tuple(
        segment.segment_id
        for segment in context.repository.segments(start.artifact_id)
        if segment.status == "succeeded"
    )
    pending = tuple(
        segment.segment_id
        for segment in context.repository.segments(start.artifact_id)
        if segment.status != "succeeded"
    )
    assert len(committed) == 40
    assert pending == ("segment-000041",)
    assert context.jobs.get(start.job_id).status == "cancelled"
    assert all(request.external_research is False for request in context.backend.requests)

    context.repository.resume(start.artifact_id, idempotency_key="translation-resume-final-batch")
    context.jobs.cancel_after_first_batch_job_id = None
    await context.service.execute(start.job_id)

    assert context.backend.generated_input_ids == [committed, pending]
    assert context.repository.get(start.artifact_id).status is TranslationStatus.SUCCEEDED
    assert context.jobs.get(start.job_id).status == "succeeded"
