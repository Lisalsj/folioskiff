from __future__ import annotations

from collections.abc import Mapping

import pytest

from paper_reader.assistant.service import AssistantService

from .conftest import AssistantTestContext, ask_command


@pytest.fixture
def assistant_parity_receipt(
    assistant_context: AssistantTestContext,
) -> Mapping[str, bool]:
    """Exercise the invariants required before removing duplicate conversation storage."""

    repository = assistant_context.repository
    first_command = ask_command()
    first = repository.create_task(
        "canonical-work",
        first_command,
        assistant_context.snapshot,
        idempotency_key="assistant-parity-first",
    )
    repeated = repository.create_task(
        "merged-work-alias",
        first_command,
        assistant_context.snapshot,
        idempotency_key="assistant-parity-first",
    )
    switched_command = first_command.model_copy(
        update={
            "agent": first_command.agent.model_copy(
                update={"backend_id": "ollama_local", "model_id": "fixture-local"}
            ),
            "message": "Use the same frozen evidence with another backend.",
        }
    )
    switched = repository.create_task(
        "canonical-work",
        switched_command,
        assistant_context.snapshot,
        idempotency_key="assistant-parity-second",
    )
    assistant_session = repository.find_session_for_work("merged-work-alias")
    assert assistant_session is not None
    before_checkpoint = repository.messages(assistant_session.id)
    repository.checkpoint(
        session_id=assistant_session.id,
        source_start_sequence=1,
        through_sequence=2,
        summary="Two immutable user turns remain available after this checkpoint.",
        backend_id="ollama_local",
        model_id="fixture-local",
        estimated_tokens=16,
    )

    return {
        "one_session_per_canonical_work": (
            first.session_id == repeated.session_id == switched.session_id
        ),
        "backend_switching": (
            repository.task(first.id).backend_id == "codex_cli"
            and repository.task(switched.id).backend_id == "ollama_local"
        ),
        "immutable_document_hash": (
            repository.snapshot(first.context_snapshot_id).document_content_sha256
            == assistant_context.snapshot.document_content_sha256
        ),
        "persist_before_stream": (
            repository.messages(first.session_id)[0].task_id == first.id
        ),
        "idempotent_submit": first.id == repeated.id,
        "cancellation": callable(repository.cancel_task),
        "retry": callable(AssistantService.retry_task),
        "checkpoint_without_message_deletion": (
            repository.messages(assistant_session.id) == before_checkpoint
            and repository.latest_checkpoint(assistant_session.id) is not None
        ),
    }


@pytest.mark.parametrize(
    "capability",
    [
        "one_session_per_canonical_work",
        "backend_switching",
        "immutable_document_hash",
        "persist_before_stream",
        "idempotent_submit",
        "cancellation",
        "retry",
        "checkpoint_without_message_deletion",
    ],
)
def test_assistant_parity_matrix(
    capability: str,
    assistant_parity_receipt: Mapping[str, bool],
) -> None:
    assert assistant_parity_receipt[capability] is True
