from __future__ import annotations

from hashlib import sha256
from typing import Any

import pytest
from sqlalchemy import func, select

from paper_reader.agents.contracts import ReasoningEffort, ServiceTier
from paper_reader.assistant.contracts import (
    AgentSelection,
    AssistantMode,
    AssistantTaskCommand,
    ContextScope,
    ContextSelection,
    NetworkPolicy,
)
from paper_reader.assistant.models import (
    AssistantContextSnapshotRecord,
    AssistantMessageRecord,
    AssistantSessionRecord,
    AssistantTaskRecord,
)
from paper_reader.assistant.repository import (
    AssistantConflictError,
    AssistantIdentityError,
    AssistantSequenceError,
    AssistantStateError,
)
from paper_reader.discovery.models import WorkRecord
from paper_reader.jobs.repository import JobRepository

AssistantTestContext = Any


def ask_command(
    *,
    document_version_id: str = "document-1",
    message: str = "What evidence supports the main claim?",
) -> AssistantTaskCommand:
    return AssistantTaskCommand(
        mode=AssistantMode.ASK,
        message=message,
        context=ContextSelection(
            document_version_id=document_version_id,
            scope=ContextScope.CURRENT_PAGE,
            page_number=1,
        ),
        agent=AgentSelection(
            backend_id="codex_cli",
            model_id="fixture-model",
            reasoning_effort=ReasoningEffort.MEDIUM,
            service_tier=ServiceTier.FLEX,
        ),
        network_policy=NetworkPolicy.LOCAL_ONLY,
        output_language="zh-CN",
    )


def _create_task(context: AssistantTestContext, key: str = "assistant-task-1"):
    return context.repository.create_task(
        "canonical-work",
        ask_command(),
        context.snapshot,
        idempotency_key=key,
    )


def test_one_canonical_work_has_one_session_and_idempotent_task(
    assistant_context: AssistantTestContext,
) -> None:
    repository = assistant_context.repository
    command = ask_command()
    snapshot = assistant_context.snapshot

    first = repository.create_task(
        "merged-work-alias",
        command,
        snapshot,
        idempotency_key="assistant-task-1",
    )
    repeated = repository.create_task(
        "canonical-work",
        command,
        snapshot,
        idempotency_key="assistant-task-1",
    )

    assert repeated.id == first.id
    assert repository.session_for_work("merged-work-alias").work_id == "canonical-work"
    messages = repository.messages_for_work("canonical-work")
    assert [(item.sequence, item.role, item.status) for item in messages] == [
        (1, "user", "committed")
    ]
    assert messages[0].content == command.message
    assert messages[0].task_id == first.id
    assert messages[0].job_id == first.job_id
    job = JobRepository(assistant_context.sessions).get(first.job_id)
    assert job is not None
    assert job.kind == "assistant.reply"
    assert job.payload["task_id"] == first.id
    assert job.payload["context_snapshot_id"] == first.context_snapshot_id


def test_idempotency_key_reuse_requires_identical_command_and_snapshot(
    assistant_context: AssistantTestContext,
) -> None:
    _create_task(assistant_context)

    with pytest.raises(AssistantConflictError, match="idempotency"):
        assistant_context.repository.create_task(
            "canonical-work",
            ask_command(message="A different question"),
            assistant_context.snapshot,
            idempotency_key="assistant-task-1",
        )
    with pytest.raises(ValueError, match="idempotency"):
        assistant_context.repository.create_task(
            "canonical-work",
            ask_command(),
            assistant_context.snapshot,
            idempotency_key="  ",
        )


def test_foreign_job_key_conflict_publishes_no_partial_assistant_state(
    assistant_context: AssistantTestContext,
) -> None:
    jobs = JobRepository(assistant_context.sessions)
    jobs.create(
        kind="unrelated.fixture",
        payload={"safe": True},
        idempotency_key="reserved-job-key",
    )

    with pytest.raises(AssistantConflictError, match="another durable job"):
        assistant_context.repository.create_task(
            "canonical-work",
            ask_command(),
            assistant_context.snapshot,
            idempotency_key="reserved-job-key",
        )

    with assistant_context.sessions() as session:
        counts = tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                AssistantSessionRecord,
                AssistantContextSnapshotRecord,
                AssistantTaskRecord,
                AssistantMessageRecord,
            )
        )
    assert counts == (0, 0, 0, 0)


def test_fragment_is_queryable_and_strictly_ordered_before_job_event(
    assistant_context: AssistantTestContext,
) -> None:
    task = _create_task(assistant_context)
    run = assistant_context.repository.create_run(task.id)
    assistant_context.repository.start_run(
        run.id,
        request_sha256="c" * 64,
        prompt_sha256="d" * 64,
    )

    persisted = assistant_context.repository.append_fragment(
        task_id=task.id,
        run_id=run.id,
        sequence=0,
        kind="text",
        text="grounded fragment",
    )
    assert assistant_context.repository.fragment(task.id, 0) == persisted
    assert (
        assistant_context.repository.append_fragment(
            task_id=task.id,
            run_id=run.id,
            sequence=0,
            kind="text",
            text="grounded fragment",
        )
        == persisted
    )
    with pytest.raises(AssistantConflictError, match="fragment"):
        assistant_context.repository.append_fragment(
            task_id=task.id,
            run_id=run.id,
            sequence=0,
            kind="text",
            text="changed fragment",
        )
    with pytest.raises(AssistantSequenceError, match="sequence"):
        assistant_context.repository.append_fragment(
            task_id=task.id,
            run_id=run.id,
            sequence=2,
            kind="text",
            text="gap",
        )


def test_run_and_task_terminal_state_cannot_be_rewritten(
    assistant_context: AssistantTestContext,
) -> None:
    repository = assistant_context.repository
    task = _create_task(assistant_context)
    run = repository.create_run(task.id)
    repository.start_run(
        run.id,
        request_sha256="c" * 64,
        prompt_sha256="d" * 64,
    )
    finished = repository.finish_run(
        run.id,
        status="succeeded",
        resolved_model_id="fixture-model-resolved",
        raw_output_sha256="e" * 64,
        usage={"input_tokens": 20, "output_tokens": 5},
    )
    assert repository.finish_run(
        run.id,
        status="succeeded",
        resolved_model_id="fixture-model-resolved",
        raw_output_sha256="e" * 64,
        usage={"input_tokens": 20, "output_tokens": 5},
    ) == finished
    with pytest.raises(AssistantStateError, match="completed run"):
        repository.finish_run(
            run.id,
            status="failed",
            resolved_model_id="fixture-model-resolved",
            raw_output_sha256=None,
            usage={},
            error_code="changed",
        )

    message = repository.commit_assistant_message(
        task_id=task.id,
        content="The selected page supports the claim.",
        citations=(
            {
                "evidence_id": "page:1:native",
                "kind": "page",
                "page_number": 1,
            },
        ),
        evidence_manifest={
            "work_id": "canonical-work",
            "document_version_id": "document-1",
            "document_content_sha256": assistant_context.snapshot.document_content_sha256,
            "evidence_sha256": assistant_context.snapshot.evidence_sha256,
            "snapshot_sha256": assistant_context.snapshot.snapshot_sha256,
        },
    )
    assert message.sequence == 2
    assert repository.task(task.id).status == "succeeded"
    job = JobRepository(assistant_context.sessions).get(task.job_id)
    assert job is not None and job.status == "succeeded" and job.progress == 100

    assert repository.commit_assistant_message(
        task_id=task.id,
        content=message.content,
        citations=message.citations,
        evidence_manifest=message.evidence_manifest,
    ) == message
    with pytest.raises(AssistantStateError, match="completed task"):
        repository.commit_assistant_message(
            task_id=task.id,
            content="A rewritten answer.",
            citations=(),
            evidence_manifest=message.evidence_manifest,
        )


def test_failure_is_terminal_and_does_not_create_assistant_message(
    assistant_context: AssistantTestContext,
) -> None:
    task = _create_task(assistant_context, "assistant-failure")
    failed = assistant_context.repository.fail_task(
        task.id,
        code="backend_unavailable",
        diagnostic="api_key=super-secret backend unavailable",
    )

    assert failed.status == "failed"
    assert failed.error_code == "backend_unavailable"
    assert failed.diagnostic is not None
    assert "super-secret" not in failed.diagnostic
    assert "[REDACTED]" in failed.diagnostic
    assert [
        item.role for item in assistant_context.repository.messages_for_work("canonical-work")
    ] == ["user"]
    job = JobRepository(assistant_context.sessions).get(task.job_id)
    assert job is not None
    assert (job.status, job.progress, job.error_code) == (
        "failed",
        100,
        "backend_unavailable",
    )
    assert assistant_context.repository.fail_task(
        task.id,
        code="backend_unavailable",
        diagnostic="api_key=super-secret backend unavailable",
    ) == failed
    with pytest.raises(AssistantStateError, match="completed task"):
        assistant_context.repository.fail_task(
            task.id,
            code="different",
            diagnostic=None,
        )


def test_checkpoint_is_immutable_and_hashes_the_summary(
    assistant_context: AssistantTestContext,
) -> None:
    task = _create_task(assistant_context)
    repository = assistant_context.repository
    checkpoint = repository.checkpoint(
        session_id=task.session_id,
        source_start_sequence=1,
        through_sequence=1,
        summary="The user asked about the paper's central evidence.",
        backend_id="codex_cli",
        model_id="fixture-model",
        estimated_tokens=12,
    )

    assert checkpoint.summary_sha256 == sha256(checkpoint.summary.encode("utf-8")).hexdigest()
    assert checkpoint.estimated_characters == len(checkpoint.summary)
    assert repository.latest_checkpoint(task.session_id) == checkpoint
    assert repository.checkpoint(
        session_id=task.session_id,
        source_start_sequence=1,
        through_sequence=1,
        summary=checkpoint.summary,
        backend_id="codex_cli",
        model_id="fixture-model",
        estimated_tokens=12,
    ) == checkpoint
    with pytest.raises(AssistantConflictError, match="checkpoint"):
        repository.checkpoint(
            session_id=task.session_id,
            source_start_sequence=1,
            through_sequence=1,
            summary="Changed history",
            backend_id="codex_cli",
            model_id="fixture-model",
            estimated_tokens=12,
        )


def test_merge_cycle_is_detected_instead_of_creating_a_second_session(
    assistant_context: AssistantTestContext,
) -> None:
    with assistant_context.sessions() as session:
        canonical = session.get(WorkRecord, "canonical-work")
        assert canonical is not None
        canonical.status = "merged"
        canonical.merged_into_work_id = "merged-work-alias"
        session.commit()

    with pytest.raises(AssistantIdentityError, match="cycle"):
        assistant_context.repository.session_for_work("merged-work-alias")
